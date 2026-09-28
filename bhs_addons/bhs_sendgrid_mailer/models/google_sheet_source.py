import json
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.google_sheet_sync_service import GoogleSheetSyncService
from ..services.google_sheets_client import GoogleSheetsClient


class BhsoftGoogleSheetSource(models.Model):
    _name = 'bhsoft.google.sheet.source'
    _description = 'Google Sheets Mail Source'
    _inherit = ['mail.thread']
    _order = 'name, id'

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    auto_sync = fields.Boolean(string='Enable Automatic Synchronization', default=False, tracking=True)
    spreadsheet_id = fields.Char(required=True, tracking=True)
    sheet_name = fields.Char(required=True, tracking=True)
    range_columns = fields.Char(
        string='Column Range', default='A:M', required=True,
        help='Column range containing headers and data, for example A:I.',
    )
    header_row = fields.Integer(default=1, required=True)
    sync_interval_minutes = fields.Integer(default=15, required=True)
    next_sync_at = fields.Datetime(index=True)

    row_id_header = fields.Char(default='Odoo Row ID', required=True)
    contact_header = fields.Char(default='Contact', required=True)
    email_header = fields.Char(default='Email', required=True)
    subject_header = fields.Char(default='Subject', required=True)
    body_header = fields.Char(default='Body', required=True)
    hubspot_id_header = fields.Char(default='HubSpot ID', required=True)
    linkedin_header = fields.Char(default='LinkedIn', required=True)
    company_header = fields.Char(default='Company', required=True)
    company_website_header = fields.Char(default='Company Website', required=True)

    service_account_email = fields.Char(compute='_compute_service_account_info')
    google_project_id = fields.Char(compute='_compute_service_account_info')
    last_sync_at = fields.Datetime(readonly=True)
    last_success_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    failure_count = fields.Integer(readonly=True)
    sync_run_ids = fields.One2many('bhsoft.google.sheet.sync.run', 'source_id', readonly=True)
    queue_ids = fields.One2many('bhsoft.mail.queue', 'sheet_source_id', readonly=True)
    queue_count = fields.Integer(compute='_compute_counts')
    sync_run_count = fields.Integer(compute='_compute_counts')
    conflict_count = fields.Integer(compute='_compute_counts')

    _positive_header_row = models.Constraint(
        'CHECK(header_row > 0)',
        'The header row must be greater than zero.',
    )
    _positive_sync_interval = models.Constraint(
        'CHECK(sync_interval_minutes > 0)',
        'The synchronization interval must be greater than zero.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        now = fields.Datetime.now()
        for vals in vals_list:
            if vals.get('auto_sync') and vals.get('active', True):
                vals.setdefault('next_sync_at', now)
        sources = super().create(vals_list)
        sources._sync_auto_sync_cron()
        return sources

    def write(self, vals):
        schedule_changed = 'active' in vals or 'auto_sync' in vals
        was_enabled = {
            source.id: source.active and source.auto_sync
            for source in self
        } if schedule_changed else {}
        result = super().write(vals)
        if schedule_changed:
            newly_enabled = self.filtered(
                lambda source: source.active
                and source.auto_sync
                and not was_enabled.get(source.id, False)
            )
            if newly_enabled:
                super(BhsoftGoogleSheetSource, newly_enabled).write({
                    'next_sync_at': fields.Datetime.now(),
                })
            self._sync_auto_sync_cron()
        return result

    def unlink(self):
        result = super().unlink()
        self._sync_auto_sync_cron()
        return result

    @api.model
    def _sync_auto_sync_cron(self):
        cron = self.env.ref(
            'bhs_sendgrid_mailer.ir_cron_sync_google_sheets',
            raise_if_not_found=False,
        )
        if not cron:
            return
        should_run = bool(self.sudo().search_count([
            ('active', '=', True),
            ('auto_sync', '=', True),
        ]))
        if cron.active != should_run:
            cron.sudo().write({'active': should_run})

    @api.depends_context('uid')
    def _compute_service_account_info(self):
        config = self.env['ir.config_parameter'].sudo()
        raw = config.get_str('bhs_sendgrid_mailer.google_service_account_json', '')
        try:
            info = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            info = {}
        for source in self:
            source.service_account_email = info.get('client_email') or False
            source.google_project_id = info.get('project_id') or False

    def _compute_counts(self):
        Queue = self.env['bhsoft.mail.queue']
        Run = self.env['bhsoft.google.sheet.sync.run']
        for source in self:
            source.queue_count = Queue.search_count([('sheet_source_id', '=', source.id)])
            source.sync_run_count = Run.search_count([('source_id', '=', source.id)])
            source.conflict_count = Queue.search_count([
                ('sheet_source_id', '=', source.id),
                ('source_conflict', '=', True),
            ])

    @api.constrains('range_columns')
    def _check_range_columns(self):
        for source in self:
            value = (source.range_columns or '').strip().upper()
            if not re.fullmatch(r'[A-Z]+:[A-Z]+', value):
                raise ValidationError(_('Column Range must use A1 column notation, for example A:M.'))

    def _get_google_credential_json(self):
        self.ensure_one()
        value = self.env['ir.config_parameter'].sudo().get_str(
            'bhs_sendgrid_mailer.google_service_account_json', ''
        )
        if not value:
            raise UserError(_('Google Service Account JSON is not configured in BHSoft Mailer Settings.'))
        return value

    def _sheet_range(self):
        self.ensure_one()
        escaped_name = self.sheet_name.replace("'", "''")
        start_column, end_column = self.range_columns.strip().upper().split(':', 1)
        return f"'{escaped_name}'!{start_column}{self.header_row}:{end_column}"

    def _column_headers(self):
        self.ensure_one()
        return {
            'row_id': self.row_id_header,
            'name': self.contact_header,
            'email': self.email_header,
            'subject': self.subject_header,
            'body': self.body_header,
            'hubspot_id': self.hubspot_id_header,
            'linkedin_url': self.linkedin_header,
            'company': self.company_header,
            'company_website': self.company_website_header,
        }

    def action_test_connection(self):
        self.ensure_one()
        client = GoogleSheetsClient(self._get_google_credential_json())
        metadata = client.get_spreadsheet(self.spreadsheet_id)
        sheet_names = {
            sheet.get('properties', {}).get('title')
            for sheet in metadata.get('sheets', [])
        }
        if self.sheet_name not in sheet_names:
            raise UserError(_(
                'Connection succeeded, but sheet "%(sheet)s" was not found.',
                sheet=self.sheet_name,
            ))
        values = client.read_values(self.spreadsheet_id, self._sheet_range())
        if not values:
            raise UserError(_('Connection succeeded, but the configured range is empty.'))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Google Sheets Connection'),
                'message': _(
                    'Connected to "%(title)s" as %(email)s.',
                    title=metadata.get('properties', {}).get('title') or self.spreadsheet_id,
                    email=client.client_email,
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_sync_now(self):
        self.ensure_one()
        run = GoogleSheetSyncService(self).sync()
        return {
            'name': _('Google Sheets Sync Run'),
            'type': 'ir.actions.act_window',
            'res_model': 'bhsoft.google.sheet.sync.run',
            'res_id': run.id,
            'view_mode': 'form',
            'target': 'current',
        }

    @api.model
    def _cron_sync_google_sheets(self):
        now = fields.Datetime.now()
        domain = [
            ('active', '=', True),
            ('auto_sync', '=', True),
            '|', ('next_sync_at', '=', False), ('next_sync_at', '<=', now),
        ]
        remaining = self.search_count(domain)
        sources = self.search(
            domain,
            order='next_sync_at asc, id asc',
            limit=10,
        )
        for source in sources:
            source.next_sync_at = now + timedelta(minutes=source.sync_interval_minutes)
            GoogleSheetSyncService(source).sync()
            remaining -= 1
            self.env['ir.cron']._commit_progress(
                processed=1,
                remaining=remaining,
            )

    def action_view_queue(self):
        self.ensure_one()
        action = self.env.ref('bhs_sendgrid_mailer.action_bhsoft_mail_queue_all').read()[0]
        action['domain'] = [('sheet_source_id', '=', self.id)]
        return action

    def action_view_sync_runs(self):
        self.ensure_one()
        action = self.env.ref('bhs_sendgrid_mailer.action_bhsoft_google_sheet_sync_runs').read()[0]
        action['domain'] = [('source_id', '=', self.id)]
        return action
