from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestMailerRetention(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Queue = self.env['bhsoft.mail.queue']
        self.Event = self.env['bhsoft.mail.event']
        self.Mail = self.env['mail.mail'].sudo()
        self.config = self.env['ir.config_parameter'].sudo()
        self.config.set_int('bhs_sendgrid_mailer.queue_retention_days', 90)
        self.config.set_int('bhs_sendgrid_mailer.event_retention_days', 30)

    def _queue(self, **values):
        defaults = {
            'name': 'Recipient',
            'email_to': 'recipient@example.com',
            'subject': 'Subject',
            'body_html': 'Body',
        }
        defaults.update(values)
        return self.Queue.create(defaults)

    def _mail(self, email='recipient@example.com'):
        return self.Mail.create({
            'subject': 'Subject',
            'body_html': '<p>Body</p>',
            'email_to': email,
        })

    def _set_write_date(self, record, value):
        self.env.cr.execute(
            f'UPDATE {record._table} SET write_date = %s WHERE id = %s',
            [value, record.id],
        )
        record.invalidate_recordset(['write_date'])

    def _set_create_date(self, record, value):
        self.env.cr.execute(
            f'UPDATE {record._table} SET create_date = %s WHERE id = %s',
            [value, record.id],
        )
        record.invalidate_recordset(['create_date'])

    def test_queue_cleanup_removes_only_expired_terminal_records(self):
        now = fields.Datetime.now()
        linked_mail = self._mail('old@example.com')
        old = self._queue(
            email_to='old@example.com',
            status='delivered',
            terminal_at=now - timedelta(days=91),
            mail_id=linked_mail.id,
        )
        event = self.Event.create({
            'queue_id': old.id,
            'event_key': 'retention-old-event',
            'event_type': 'delivered',
            'event_time': now - timedelta(days=91),
            'source': 'webhook',
        })
        recent = self._queue(
            email_to='recent@example.com',
            status='delivered',
            terminal_at=now - timedelta(days=89),
        )
        pending = self._queue(
            email_to='pending@example.com',
            terminal_at=now - timedelta(days=120),
        )
        processing = self._queue(
            email_to='processing@example.com',
            status='processing',
            terminal_at=now - timedelta(days=120),
        )
        queued = self._queue(
            email_to='queued@example.com',
            status='queued',
            terminal_at=now - timedelta(days=120),
        )
        unknown = self._queue(
            email_to='unknown@example.com',
            status='unknown',
            terminal_at=now - timedelta(days=120),
        )

        self.Queue._gc_mailer_data()

        self.assertFalse(old.exists())
        self.assertFalse(event.exists())
        self.assertFalse(linked_mail.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(pending.exists())
        self.assertTrue(processing.exists())
        self.assertTrue(queued.exists())
        self.assertTrue(unknown.exists())

    def test_sheet_queue_is_compacted_once_and_preserves_identity(self):
        self.config.set_int('bhs_sendgrid_mailer.event_retention_days', 0)
        now = fields.Datetime.now()
        source = self.env['bhsoft.google.sheet.source'].create({
            'name': 'Retention Sheet',
            'spreadsheet_id': 'retention-sheet',
            'sheet_name': 'Contacts',
            'range_columns': 'A:I',
        })
        linked_mail = self._mail('sheet@example.com')
        queue = self._queue(
            name='Sheet Recipient',
            email_to='sheet@example.com',
            company_name='Acme',
            subject='Private subject',
            status='delivered',
            terminal_at=now - timedelta(days=91),
            sheet_source_id=source.id,
            sheet_row_id='row-1',
            source_hash='source-hash',
            content_hash='content-hash',
            hubspot_id='hubspot-1',
            linkedin_url='https://linkedin.test/sheet',
            company_website='https://acme.test',
            source_conflict_message='old conflict detail',
            webhook_log='old webhook log',
            api_sync_log='old API log',
            mail_id=linked_mail.id,
        )

        self.Queue._gc_mailer_data()

        self.assertTrue(queue.exists())
        self.assertEqual(queue.name, 'Archived recipient')
        self.assertEqual(queue.subject, '[Archived email subject]')
        self.assertEqual(queue.body_html, '[Archived email body]')
        self.assertFalse(queue.company_name)
        self.assertFalse(queue.hubspot_id)
        self.assertFalse(queue.linkedin_url)
        self.assertFalse(queue.company_website)
        self.assertFalse(queue.source_conflict_message)
        self.assertTrue(queue.compacted_at)
        self.assertFalse(queue.webhook_log)
        self.assertFalse(queue.api_sync_log)
        self.assertFalse(queue.mail_id)
        self.assertFalse(linked_mail.exists())
        self.assertEqual(queue.sheet_source_id, source)
        self.assertEqual(queue.sheet_row_id, 'row-1')
        self.assertEqual(queue.source_hash, 'source-hash')
        self.assertEqual(queue.content_hash, 'content-hash')

        compacted_at = queue.compacted_at
        queue.write({'body_html': 'Do not compact twice'})
        self.Queue._gc_mailer_data()

        self.assertEqual(queue.body_html, 'Do not compact twice')
        self.assertEqual(queue.compacted_at, compacted_at)

    def test_event_and_text_log_cleanup_respects_cutoff(self):
        fixed_now = fields.Datetime.now().replace(microsecond=0)
        cutoff = fixed_now - timedelta(days=30)
        old_queue = self._queue(
            email_to='old-log@example.com',
            status='delivered',
            terminal_at=fixed_now,
            webhook_log='old webhook log',
            api_sync_log='old API log',
        )
        recent_queue = self._queue(
            email_to='recent-log@example.com',
            status='delivered',
            terminal_at=fixed_now,
            webhook_log='recent webhook log',
            api_sync_log='recent API log',
        )
        unknown_queue = self._queue(
            email_to='unknown-log@example.com',
            status='unknown',
            webhook_log='old unknown webhook log',
            api_sync_log='old unknown API log',
        )
        old_event = self.Event.create({
            'queue_id': old_queue.id,
            'event_key': 'retention-cutoff-event',
            'event_type': 'delivered',
            'event_time': cutoff,
            'source': 'webhook',
        })
        recent_event = self.Event.create({
            'queue_id': recent_queue.id,
            'event_key': 'retention-recent-event',
            'event_type': 'delivered',
            'event_time': cutoff + timedelta(seconds=1),
            'source': 'webhook',
        })
        self._set_create_date(old_event, cutoff)
        self._set_create_date(recent_event, cutoff + timedelta(seconds=1))
        self._set_write_date(old_queue, cutoff)
        self._set_write_date(recent_queue, cutoff + timedelta(seconds=1))
        self._set_write_date(unknown_queue, cutoff)

        with patch(
            'odoo.addons.bhs_sendgrid_mailer.models.mail_queue.fields.Datetime.now',
            return_value=fixed_now,
        ):
            self.Queue._gc_mailer_data()

        self.assertFalse(old_event.exists())
        self.assertTrue(recent_event.exists())
        self.assertFalse(old_queue.webhook_log)
        self.assertFalse(old_queue.api_sync_log)
        self.assertEqual(recent_queue.webhook_log, 'recent webhook log')
        self.assertEqual(recent_queue.api_sync_log, 'recent API log')
        self.assertFalse(unknown_queue.webhook_log)
        self.assertFalse(unknown_queue.api_sync_log)
        self.assertEqual(unknown_queue.status, 'unknown')

    def test_zero_retention_disables_queue_event_and_log_cleanup(self):
        self.config.set_int('bhs_sendgrid_mailer.queue_retention_days', 0)
        self.config.set_int('bhs_sendgrid_mailer.event_retention_days', 0)
        old_date = fields.Datetime.now() - timedelta(days=400)
        queue = self._queue(
            status='delivered',
            terminal_at=old_date,
            webhook_log='keep webhook log',
            api_sync_log='keep API log',
        )
        event = self.Event.create({
            'queue_id': queue.id,
            'event_key': 'retention-disabled-event',
            'event_type': 'delivered',
            'event_time': old_date,
            'source': 'webhook',
        })
        self._set_create_date(event, old_date)
        self._set_write_date(queue, old_date)

        self.Queue._gc_mailer_data()

        self.assertTrue(queue.exists())
        self.assertTrue(event.exists())
        self.assertEqual(queue.webhook_log, 'keep webhook log')
        self.assertEqual(queue.api_sync_log, 'keep API log')


@tagged('post_install', '-at_install')
class TestGoogleSheetAuditRetention(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Run = self.env['bhsoft.google.sheet.sync.run']
        self.Line = self.env['bhsoft.google.sheet.sync.line']
        self.config = self.env['ir.config_parameter'].sudo()
        self.config.set_int('bhs_sendgrid_mailer.sync_line_retention_days', 30)
        self.config.set_int('bhs_sendgrid_mailer.sync_run_retention_days', 365)
        self.source = self.env['bhsoft.google.sheet.source'].create({
            'name': 'Audit Retention Sheet',
            'spreadsheet_id': 'audit-retention-sheet',
            'sheet_name': 'Contacts',
            'range_columns': 'A:I',
        })

    def _run(self, **values):
        defaults = {
            'source_id': self.source.id,
            'state': 'success',
            'finished_at': fields.Datetime.now(),
        }
        defaults.update(values)
        return self.Run.create(defaults)

    def _line(self, run, row_number):
        return self.Line.create({
            'run_id': run.id,
            'row_number': row_number,
            'result': 'unchanged',
        })

    def _set_create_date(self, record, value):
        self.env.cr.execute(
            f'UPDATE {record._table} SET create_date = %s WHERE id = %s',
            [value, record.id],
        )
        record.invalidate_recordset(['create_date'])

    def test_audit_cleanup_orders_lines_before_completed_runs(self):
        now = fields.Datetime.now()
        old_finished = now - timedelta(days=366)
        old_line_date = now - timedelta(days=31)

        expired_run = self._run(finished_at=old_finished)
        expired_line = self._line(expired_run, 2)
        self._set_create_date(expired_line, old_line_date)

        blocked_run = self._run(finished_at=old_finished)
        recent_line = self._line(blocked_run, 3)

        recent_run = self._run(finished_at=now - timedelta(days=364))
        old_line = self._line(recent_run, 4)
        self._set_create_date(old_line, old_line_date)

        running_run = self._run(state='running', finished_at=False)

        self.Run._gc_google_sheet_sync_audit()

        self.assertFalse(expired_line.exists())
        self.assertFalse(expired_run.exists())
        self.assertTrue(recent_line.exists())
        self.assertTrue(blocked_run.exists())
        self.assertFalse(old_line.exists())
        self.assertTrue(recent_run.exists())
        self.assertTrue(running_run.exists())

    def test_zero_audit_retention_disables_cleanup(self):
        self.config.set_int('bhs_sendgrid_mailer.sync_line_retention_days', 0)
        self.config.set_int('bhs_sendgrid_mailer.sync_run_retention_days', 0)
        old_date = fields.Datetime.now() - timedelta(days=800)
        run = self._run(finished_at=old_date)
        line = self._line(run, 2)
        self._set_create_date(line, old_date)

        self.Run._gc_google_sheet_sync_audit()

        self.assertTrue(line.exists())
        self.assertTrue(run.exists())
