from datetime import timedelta

from odoo import api, fields, models


class BhsoftGoogleSheetSyncRun(models.Model):
    _name = 'bhsoft.google.sheet.sync.run'
    _description = 'Google Sheets Synchronization Run'
    _order = 'started_at desc, id desc'

    source_id = fields.Many2one(
        'bhsoft.google.sheet.source', required=True, readonly=True, ondelete='cascade', index=True,
    )
    started_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    finished_at = fields.Datetime(readonly=True)
    state = fields.Selection([
        ('running', 'Running'),
        ('success', 'Success'),
        ('partial', 'Partial Success'),
        ('failed', 'Failed'),
    ], required=True, readonly=True, default='running', index=True)
    total_count = fields.Integer(readonly=True)
    created_count = fields.Integer(readonly=True)
    updated_count = fields.Integer(readonly=True)
    unchanged_count = fields.Integer(readonly=True)
    duplicate_count = fields.Integer(readonly=True)
    skipped_count = fields.Integer(readonly=True)
    conflict_count = fields.Integer(readonly=True)
    error_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    line_ids = fields.One2many('bhsoft.google.sheet.sync.line', 'run_id', readonly=True)

    @api.autovacuum
    def _gc_google_sheet_sync_audit(self):
        config = self.env['ir.config_parameter'].sudo()
        now = fields.Datetime.now()
        line_days = config.get_int('bhs_sendgrid_mailer.sync_line_retention_days', 30)
        run_days = config.get_int('bhs_sendgrid_mailer.sync_run_retention_days', 365)
        if line_days > 0:
            line_deadline = now - timedelta(days=line_days)
            lines = self.env['bhsoft.google.sheet.sync.line'].sudo().search([
                ('create_date', '<=', line_deadline),
            ], order='id asc', limit=10_000)
            line_count = len(lines)
            lines.with_context(prefetch_fields=False).unlink()
        else:
            line_count = 0
        if run_days > 0:
            run_deadline = now - timedelta(days=run_days)
            runs = self.sudo().search([
                ('finished_at', '!=', False),
                ('finished_at', '<=', run_deadline),
                ('line_ids', '=', False),
            ], order='id asc', limit=10_000)
            run_count = len(runs)
            runs.with_context(prefetch_fields=False).unlink()
        else:
            run_count = 0
        return line_count + run_count, max(
            line_count == 10_000,
            run_count == 10_000,
        )


class BhsoftGoogleSheetSyncLine(models.Model):
    _name = 'bhsoft.google.sheet.sync.line'
    _description = 'Google Sheets Synchronization Row Result'
    _order = 'row_number, id'

    run_id = fields.Many2one(
        'bhsoft.google.sheet.sync.run', required=True, readonly=True, ondelete='cascade', index=True,
    )
    source_id = fields.Many2one(related='run_id.source_id', store=True, index=True)
    row_number = fields.Integer(required=True, readonly=True)
    row_id = fields.Char(readonly=True, index=True)
    email = fields.Char(readonly=True)
    result = fields.Selection([
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('unchanged', 'Unchanged'),
        ('duplicate', 'Duplicate'),
        ('conflict', 'Conflict'),
        ('error', 'Error'),
        ('skipped', 'Skipped'),
    ], required=True, readonly=True, index=True)
    message = fields.Text(readonly=True)
    queue_id = fields.Many2one('bhsoft.mail.queue', readonly=True, ondelete='set null', index=True)
