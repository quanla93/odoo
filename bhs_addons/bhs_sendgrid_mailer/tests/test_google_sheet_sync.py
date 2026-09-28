from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..services.google_sheet_sync_service import GoogleSheetSyncService


@tagged('post_install', '-at_install')
class TestGoogleSheetSync(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.google_service_account_json',
            '{"type":"service_account","client_email":"sync@example.test","private_key":"key"}',
        )
        self.source = self.env['bhsoft.google.sheet.source'].create({
            'name': 'Test Sheet',
            'spreadsheet_id': 'spreadsheet-id',
            'sheet_name': 'Contacts',
            'range_columns': 'A:I',
        })
        self.headers = [
            'Odoo Row ID', 'Contact', 'Email', 'Subject', 'Body',
            'HubSpot ID', 'LinkedIn', 'Company', 'Company Website',
        ]

    def _sync(self, rows):
        with patch(
            'odoo.addons.bhs_sendgrid_mailer.services.google_sheet_sync_service.GoogleSheetsClient'
        ) as Client:
            client = Client.return_value
            client.read_values.return_value = [list(self.headers), *rows]
            run = GoogleSheetSyncService(self.source).sync()
            return run, client

    def _google_sync_cron(self):
        return self.env.ref(
            'bhs_sendgrid_mailer.ir_cron_sync_google_sheets'
        ).sudo()

    def test_creating_automatic_source_activates_cron_and_is_due(self):
        cron = self._google_sync_cron()
        cron.active = False

        source = self.env['bhsoft.google.sheet.source'].create({
            'name': 'Automatic Sheet',
            'spreadsheet_id': 'automatic-spreadsheet-id',
            'sheet_name': 'Contacts',
            'range_columns': 'A:I',
            'auto_sync': True,
        })

        self.assertTrue(cron.active)
        self.assertTrue(source.next_sync_at)
        self.assertLessEqual(source.next_sync_at, fields.Datetime.now())

    def test_auto_sync_setting_controls_shared_cron(self):
        cron = self._google_sync_cron()
        cron.active = False

        self.source.auto_sync = True
        self.assertTrue(cron.active)
        self.assertTrue(self.source.next_sync_at)

        self.source.auto_sync = False
        self.assertFalse(cron.active)

    def test_cron_remains_active_while_another_source_is_automatic(self):
        self.source.auto_sync = True
        other = self.env['bhsoft.google.sheet.source'].create({
            'name': 'Other Sheet',
            'spreadsheet_id': 'other-spreadsheet-id',
            'sheet_name': 'Contacts',
            'range_columns': 'A:I',
            'auto_sync': True,
        })

        self.source.auto_sync = False

        self.assertTrue(self._google_sync_cron().active)
        other.auto_sync = False
        self.assertFalse(self._google_sync_cron().active)

    def test_archiving_automatic_source_updates_cron(self):
        self.source.auto_sync = True
        cron = self._google_sync_cron()

        self.source.active = False
        self.assertFalse(cron.active)

        self.source.active = True
        self.assertTrue(cron.active)
        self.assertTrue(self.source.next_sync_at)
        self.assertLessEqual(self.source.next_sync_at, fields.Datetime.now())

    def test_sheet_range_uses_valid_a1_notation(self):
        self.assertEqual(self.source._sheet_range(), "'Contacts'!A1:I")

    def test_missing_row_id_header_is_created(self):
        self.headers = [
            'Contact', 'Email', 'Subject', 'Body', 'HubSpot ID',
            'LinkedIn', 'Company', 'Company Website',
        ]
        run, client = self._sync([
            ['Alice', 'alice@example.com', 'Hello', 'Body', '1', '', 'Acme', 'https://acme.test'],
        ])
        self.assertEqual(run.state, 'success')
        client.write_values.assert_called_once_with(
            self.source.spreadsheet_id,
            [("'Contacts'!I1", 'Odoo Row ID')],
        )
        client.write_row_ids.assert_called_once()

    def test_missing_row_id_is_written_and_imported(self):
        run, client = self._sync([
            ['', 'Alice', 'Alice@Example.com', 'Hello', 'Body', '1', '', 'Acme', 'https://acme.test'],
        ])
        queue = self.env['bhsoft.mail.queue'].search([('sheet_source_id', '=', self.source.id)])
        self.assertEqual(run.state, 'success')
        self.assertEqual(run.created_count, 1)
        self.assertTrue(queue.sheet_row_id)
        self.assertEqual(queue.normalized_email, 'alice@example.com')
        client.write_row_ids.assert_called_once()

    def _use_operational_headers(self):
        self.headers = [
            'No.', 'Sending Status', 'Contact', 'Email', 'Subject',
            'Body', 'HubSpot ID', 'LinkedIn', 'Company',
            'Company Website', 'Sent date', 'Sent time',
            'Validation errors', 'Odoo Row ID',
        ]
        self.source.range_columns = 'A:N'

    def test_rows_without_business_data_are_ignored(self):
        self._use_operational_headers()
        run, client = self._sync([
            ['2', 'Wait', '', '', '', '', '', '', '', '', '', '', '', ''],
            ['3', 'Wait', '', '', '', '', '', '', '', '', '', '', '', ''],
        ])

        self.assertEqual(run.state, 'success')
        self.assertEqual(run.total_count, 0)
        self.assertEqual(run.error_count, 0)
        self.assertFalse(run.line_ids)
        self.assertFalse(self.env['bhsoft.mail.queue'].search([
            ('sheet_source_id', '=', self.source.id),
        ]))
        client.write_row_ids.assert_not_called()

    def test_mixed_rows_assign_id_only_to_meaningful_data(self):
        self._use_operational_headers()
        run, client = self._sync([
            ['1', 'Wait', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', '', '', '', '', ''],
            ['2', 'Wait', '', '', '', '', '', '', '', '', '', '', '', ''],
            ['3', 'Wait', '', '', '', '', '', '', '', '', '', '', '', ''],
        ])

        self.assertEqual(run.state, 'success')
        self.assertEqual(run.total_count, 1)
        self.assertEqual(run.created_count, 1)
        self.assertEqual(run.error_count, 0)
        updates = client.write_row_ids.call_args.args[1]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][0], "'Contacts'!N2")

    def test_sheet_body_is_stored_as_exact_plain_text(self):
        body = 'Hello <Alice> & team,\n\nFirst paragraph.\nSecond line.\n\nRegards,\nBHSoft'
        run, _client = self._sync([
            ['', 'Alice', 'alice@example.com', 'Plain text message', body, '', '', 'Acme', ''],
        ])

        queue = self.env['bhsoft.mail.queue'].search([
            ('sheet_source_id', '=', self.source.id),
        ])
        self.assertEqual(run.state, 'success')
        self.assertEqual(run.created_count, 1)
        self.assertEqual(queue.body_html, body)

    def test_body_newline_change_updates_pending_row(self):
        row = [
            'row-1', 'Alice', 'alice@example.com', 'Hello',
            'First line\nSecond line', '', '', 'Acme', '',
        ]
        self._sync([row])
        queue = self.env['bhsoft.mail.queue'].search([
            ('sheet_source_id', '=', self.source.id),
        ])
        original_hash = queue.content_hash

        row[4] = 'First line\n\nSecond line'
        run, _client = self._sync([row])

        self.assertEqual(run.updated_count, 1)
        self.assertEqual(queue.body_html, row[4])
        self.assertNotEqual(queue.content_hash, original_hash)

    def test_meaningful_row_without_email_remains_an_error(self):
        run, client = self._sync([
            ['', 'Alice', '', 'Hello', 'Body', '', '', '', ''],
        ])

        self.assertEqual(run.state, 'partial')
        self.assertEqual(run.total_count, 1)
        self.assertEqual(run.error_count, 1)
        self.assertEqual(run.line_ids.result, 'error')
        self.assertEqual(run.line_ids.message, 'Email is required.')
        client.write_row_ids.assert_called_once()

    def test_repeated_sync_updates_pending_instead_of_creating(self):
        row = ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', '']
        self._sync([row])
        row[3] = 'Updated subject'
        run, _client = self._sync([row])
        queues = self.env['bhsoft.mail.queue'].search([('sheet_source_id', '=', self.source.id)])
        self.assertEqual(len(queues), 1)
        self.assertEqual(queues.subject, 'Updated subject')
        self.assertEqual(run.updated_count, 1)

    def test_changed_non_pending_row_becomes_conflict(self):
        row = ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', '']
        self._sync([row])
        queue = self.env['bhsoft.mail.queue'].search([('sheet_source_id', '=', self.source.id)])
        queue.status = 'queued'
        row[4] = 'Changed body'
        run, _client = self._sync([row])
        self.assertEqual(run.conflict_count, 1)
        self.assertTrue(queue.source_conflict)
        self.assertEqual(queue.body_html, 'Body')

    def test_same_email_and_content_is_duplicate(self):
        rows = [
            ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', ''],
            ['row-2', 'Alice 2', 'ALICE@example.com ', 'Hello', 'Body', '', '', 'Acme', ''],
        ]
        run, _client = self._sync(rows)
        queues = self.env['bhsoft.mail.queue'].search(
            [('sheet_source_id', '=', self.source.id)], order='id asc'
        )
        self.assertEqual(run.duplicate_count, 1)
        self.assertEqual(queues[1].status, 'duplicate')
        self.assertEqual(queues[1].duplicate_of_id, queues[0])

    def test_duplicate_row_ids_are_quarantined(self):
        rows = [
            ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', ''],
            ['row-1', 'Bob', 'bob@example.com', 'Hello', 'Body', '', '', 'Beta', ''],
        ]
        run, _client = self._sync(rows)
        self.assertEqual(run.error_count, 2)
        self.assertFalse(self.env['bhsoft.mail.queue'].search([('sheet_source_id', '=', self.source.id)]))

    def test_duplicate_row_id_does_not_mark_existing_queue_missing(self):
        self._sync([
            ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', ''],
        ])
        run, _client = self._sync([
            ['row-1', 'Alice', 'alice@example.com', 'Hello', 'Body', '', '', 'Acme', ''],
            ['row-1', 'Bob', 'bob@example.com', 'Hello', 'Body', '', '', 'Beta', ''],
        ])
        queue = self.env['bhsoft.mail.queue'].search([
            ('sheet_source_id', '=', self.source.id),
            ('sheet_row_id', '=', 'row-1'),
        ])
        self.assertEqual(run.error_count, 2)
        self.assertFalse(queue.source_missing)

    def test_missing_credentials_creates_failed_run(self):
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.google_service_account_json', '',
        )
        run = GoogleSheetSyncService(self.source).sync()
        self.assertEqual(run.state, 'failed')
        self.assertIn('not configured', run.error_message)
