import io

from odoo.tests import TransactionCase, tagged
from odoo.tools.binary import BinaryBytes

try:
    import openpyxl
except ImportError:
    openpyxl = None


@tagged('post_install', '-at_install')
class TestMailQueueImport(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Queue = self.env['bhsoft.mail.queue']
        self.config = self.env['ir.config_parameter'].sudo()
        self.config.set_str(
            'bhs_sendgrid_mailer.dedupe_mode', 'batch_email',
        )

    def _import_rows(self, rows):
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['Contact', 'Email', 'Subject', 'Body', 'Company'])
        for row in rows:
            sheet.append(row)
        stream = io.BytesIO()
        workbook.save(stream)
        wizard = self.env['bhsoft.mail.queue.import.wizard'].create({
            'file': BinaryBytes(
                stream.getvalue(), filename='recipients.xlsx',
            ),
            'file_name': 'recipients.xlsx',
        })
        action = wizard.action_import()
        batch_id = action['domain'][0][2]
        records = self.Queue.search(
            [('import_batch_id', '=', batch_id)], order='source_row_number asc'
        )
        return action, records

    def test_multiline_body_is_preserved_as_plain_text(self):
        body = 'Hello <Alice> & team,\n\nFirst line.\nSecond line.'

        _action, records = self._import_rows([
            ['Alice', 'alice@example.com', 'Hello', body, 'Acme'],
        ])

        self.assertEqual(records.body_html, body)

    def test_repeated_email_is_marked_duplicate(self):
        action, records = self._import_rows([
            ['Alice', ' Alice@Example.com ', 'First subject', 'First body', 'Acme'],
            ['Alice 2', 'alice@example.com', 'Second subject', 'Second body', 'Acme'],
        ])

        self.assertEqual(records.mapped('status'), ['none', 'duplicate'])
        self.assertEqual(records[1].duplicate_of_id, records[0])
        self.assertIn('alice@example.com', records[1].error_message)
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['views'], [(False, 'list'), (False, 'form')])
        self.assertEqual(action['res_model'], 'bhsoft.mail.queue')

    def test_distinct_emails_remain_pending(self):
        _action, records = self._import_rows([
            ['Alice', 'alice@example.com', 'Hello', 'Body', 'Acme'],
            ['Bob', 'bob@example.com', 'Hello', 'Body', 'Beta'],
        ])

        self.assertEqual(records.mapped('status'), ['none', 'none'])
        self.assertFalse(records.mapped('duplicate_of_id'))

    def test_dedupe_disabled_keeps_repeated_email_pending(self):
        self.config.set_str('bhs_sendgrid_mailer.dedupe_mode', 'none')

        _action, records = self._import_rows([
            ['Alice', 'alice@example.com', 'Hello', 'Same body', 'Acme'],
            ['Alice 2', 'ALICE@example.com', 'Hello', 'Same body', 'Acme'],
        ])

        self.assertEqual(records.mapped('status'), ['none', 'none'])
        self.assertFalse(records.mapped('duplicate_of_id'))

    def test_invalid_email_is_failed_and_missing_email_is_skipped(self):
        action, records = self._import_rows([
            ['Alice', 'not-an-email', 'Hello', 'Body', 'Acme'],
            ['Bob', None, 'Hello', 'Body', 'Beta'],
        ])

        self.assertEqual(len(records), 1)
        self.assertEqual(records.status, 'failed')
        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'bhsoft.mail.queue')
