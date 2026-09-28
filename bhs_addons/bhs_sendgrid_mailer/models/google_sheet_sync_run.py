from odoo import fields, models


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
    conflict_count = fields.Integer(readonly=True)
    error_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    line_ids = fields.One2many('bhsoft.google.sheet.sync.line', 'run_id', readonly=True)


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
