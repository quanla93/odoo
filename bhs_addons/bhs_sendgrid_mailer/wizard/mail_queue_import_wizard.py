from odoo import models, fields, _
from odoo.exceptions import UserError
import base64
import hashlib
import io
import re
import uuid

try:
    import openpyxl
except ImportError:
    openpyxl = None


class MailQueueImportWizard(models.TransientModel):
    _name = 'bhsoft.mail.queue.import.wizard'
    _description = 'Excel Mail Queue Import Wizard'

    file = fields.Binary(string='Excel File (.xlsx)', required=True)
    file_name = fields.Char(string='File Name')

    def _find_column(self, headers, names):
        for idx, header in enumerate(headers):
            if header in names:
                return idx
        return -1

    def action_import(self):
        if not openpyxl:
            raise UserError(_('The openpyxl library is not installed. Please install it on the server.'))

        if not self.file:
            raise UserError(_('Please select an Excel file.'))

        try:
            file_data = self.file.content if hasattr(self.file, 'content') else base64.b64decode(self.file)
            workbook = openpyxl.load_workbook(filename=io.BytesIO(file_data), data_only=True)
            sheet = workbook.active
        except Exception as e:
            raise UserError(_(
                'Unable to read the Excel file. Please ensure it is a valid .xlsx file.\nError details: %(error)s',
                error=str(e),
            ))

        headers = [str(cell.value or '').strip().lower() for cell in sheet[1]]

        col_name = self._find_column(headers, ['tên', 'name', 'người nhận', 'họ tên', 'recipient', 'contact'])
        col_email = self._find_column(headers, ['email', 'email nhận', 'email to', 'to'])
        col_company = self._find_column(headers, ['công ty', 'cong ty', 'company', 'company_name', 'organization', 'organisation'])
        col_subject = self._find_column(headers, ['tiêu đề', 'subject', 'chủ đề'])
        col_body = self._find_column(headers, ['nội dung', 'body', 'body_html', 'content', 'nội dung email'])
        col_hubspot = self._find_column(headers, ['hubspot id', 'hubspot_id', 'hubspot'])
        col_linkedin = self._find_column(headers, ['linkedin', 'linkedin url', 'linkedin_url'])
        col_company_website = self._find_column(headers, ['company website', 'website', 'company_website'])

        if col_email == -1 or col_subject == -1:
            raise UserError(_(
                'The Excel file is missing required columns. It must contain at least "Email" and "Subject" columns.'
            ))

        Queue = self.env['bhsoft.mail.queue']
        config = self.env['ir.config_parameter'].sudo()
        dedupe_mode = config.get_str('bhs_sendgrid_mailer.dedupe_mode', 'batch_email')
        import_batch_id = str(uuid.uuid4())
        vals_list = []
        first_by_email = {}

        for row_index, row in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
            if not row[col_email]:
                continue

            name = row[col_name] if col_name != -1 and row[col_name] else _('Customer')
            email = str(row[col_email]).strip()
            normalized_email = Queue._normalize_email(email)
            company = row[col_company] if col_company != -1 and row[col_company] else ''
            subject = row[col_subject]
            body = row[col_body] if col_body != -1 and row[col_body] else ''
            hubspot_id = row[col_hubspot] if col_hubspot != -1 and row[col_hubspot] else ''
            linkedin_url = row[col_linkedin] if col_linkedin != -1 and row[col_linkedin] else ''
            company_website = row[col_company_website] if col_company_website != -1 and row[col_company_website] else ''
            canonical_subject = re.sub(r'\s+', ' ', str(subject or '').strip())
            body_text = str(body or '')
            content_hash = hashlib.sha256(
                '\x1f'.join((normalized_email, canonical_subject, body_text)).encode('utf-8')
            ).hexdigest()

            vals = {
                'name': str(name),
                'email_to': email,
                'company_name': str(company) if company else False,
                'subject': str(subject),
                'body_html': body_text,
                'hubspot_id': str(hubspot_id) if hubspot_id else False,
                'linkedin_url': str(linkedin_url) if linkedin_url else False,
                'company_website': str(company_website) if company_website else False,
                'content_hash': content_hash,
                'status': 'none',
                'source_row_uuid': str(uuid.uuid4()),
                'source_row_number': row_index,
                'import_batch_id': import_batch_id,
            }

            if dedupe_mode == 'batch_email' and normalized_email:
                if normalized_email in first_by_email:
                    vals.update({
                        'status': 'duplicate',
                        'duplicate_of_id': first_by_email[normalized_email],
                        'error_message': _(
                            'Skipped because this email is duplicated within this import: %(email)s',
                            email=email,
                        ),
                    })
                else:
                    first_by_email[normalized_email] = False

            vals_list.append(vals)

        suppressions = self.env['bhsoft.email.suppression'].sudo().active_by_email(
            [vals.get('email_to') for vals in vals_list]
        )
        for vals in vals_list:
            suppression = suppressions.get(Queue._normalize_email(vals.get('email_to')))
            if suppression:
                vals.update({
                    'status': 'skipped',
                    'duplicate_of_id': False,
                    'suppression_id': suppression.id,
                    'terminal_at': fields.Datetime.now(),
                    'error_message': _(
                        'Skipped because this recipient is suppressed: %(reason)s',
                        reason=suppression.reason,
                    ),
                })

        created_records = Queue.create(vals_list) if vals_list else Queue.browse()

        if dedupe_mode == 'batch_email':
            originals = {}
            for record in created_records.sorted('source_row_number'):
                if record.normalized_email and record.status != 'duplicate':
                    originals.setdefault(record.normalized_email, record.id)
            for record in created_records.filtered(
                lambda item: item.status == 'duplicate' and item.normalized_email
            ):
                original_id = originals.get(record.normalized_email)
                if original_id:
                    record.write({'duplicate_of_id': original_id})

        return {
            'name': _('Mail Queue'),
            'type': 'ir.actions.act_window',
            'res_model': 'bhsoft.mail.queue',
            'view_mode': 'list,form',
            'views': [(False, 'list'), (False, 'form')],
            'domain': [('import_batch_id', '=', import_batch_id)],
            'target': 'main',
        }
