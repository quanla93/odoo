from datetime import datetime, timezone

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


SUPPRESSION_EVENT_REASONS = {
    'bounce': 'hard_bounce',
    'spamreport': 'spam_report',
    'unsubscribe': 'unsubscribe',
    'group_unsubscribe': 'unsubscribe',
}


class BhsoftEmailSuppression(models.Model):
    _name = 'bhsoft.email.suppression'
    _description = 'Suppressed Email Address'
    _order = 'active desc, last_event_at desc, id desc'

    normalized_email = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True, required=True, index=True)
    reason = fields.Selection([
        ('hard_bounce', 'Hard Bounce'),
        ('spam_report', 'Spam Report'),
        ('unsubscribe', 'Unsubscribe'),
        ('manual', 'Manual Block'),
    ], required=True, default='manual', index=True)
    first_event_at = fields.Datetime(readonly=True)
    last_event_at = fields.Datetime(readonly=True, index=True)
    provider = fields.Selection([
        ('sendgrid', 'SendGrid'),
        ('manual', 'Manual'),
        ('migration', 'Migration'),
    ], required=True, default='manual', readonly=True)
    last_provider_event_id = fields.Char(readonly=True)
    last_detail = fields.Text(readonly=True)
    deactivated_at = fields.Datetime(readonly=True, index=True)

    _normalized_email_unique = models.UniqueIndex(
        '(normalized_email)',
        'This email address already exists in the suppression list.',
    )

    @api.model
    def _normalize_email(self, email):
        return (email or '').strip().lower()

    @api.model
    def _reason_from_sendgrid_event(self, event_type):
        return SUPPRESSION_EVENT_REASONS.get((event_type or '').lower())

    @api.model
    def _event_datetime(self, event_data):
        raw_timestamp = (event_data or {}).get('timestamp') or (event_data or {}).get('processed')
        if isinstance(raw_timestamp, (int, float)):
            return datetime.fromtimestamp(raw_timestamp, timezone.utc).replace(tzinfo=None)
        if isinstance(raw_timestamp, str):
            try:
                value = raw_timestamp.strip()
                if value.isdigit():
                    return datetime.fromtimestamp(int(value), timezone.utc).replace(tzinfo=None)
                parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.tzinfo:
                    return parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except (TypeError, ValueError, OverflowError):
                pass
        return fields.Datetime.now()

    @api.model
    def active_by_email(self, emails):
        normalized = {
            self._normalize_email(email)
            for email in emails
            if self._normalize_email(email)
        }
        if not normalized:
            return {}
        records = self.sudo().search([
            ('normalized_email', 'in', list(normalized)),
            ('active', '=', True),
        ])
        return {record.normalized_email: record for record in records}

    @api.model
    def active_for_email(self, email):
        return self.active_by_email([email]).get(self._normalize_email(email)) or self.browse()

    @api.model
    def upsert_from_sendgrid_event(self, email, event_type, event_data=None):
        reason = self._reason_from_sendgrid_event(event_type)
        normalized_email = self._normalize_email(email)
        if not reason or not normalized_email:
            return self.browse()

        event_data = event_data or {}
        event_time = self._event_datetime(event_data)
        self.flush_model([
            'normalized_email', 'active', 'last_event_at', 'deactivated_at',
        ])
        self.env.cr.execute("""
            SELECT id, active, last_event_at, deactivated_at
              FROM bhsoft_email_suppression
             WHERE normalized_email = %s
             LIMIT 1
        """, [normalized_email])
        existing_row = self.env.cr.fetchone()
        if (
            existing_row
            and not existing_row[1]
            and (
                (existing_row[3] and event_time <= existing_row[3])
                or (existing_row[2] and event_time <= existing_row[2])
            )
        ):
            return self.browse(existing_row[0])
        provider_event_id = event_data.get('sg_event_id') or event_data.get('event_id') or None
        detail = event_data.get('reason') or event_data.get('response') or None
        self.env.cr.execute("""
            INSERT INTO bhsoft_email_suppression (
                normalized_email, active, reason, first_event_at, last_event_at,
                provider, last_provider_event_id, last_detail,
                create_uid, write_uid, create_date, write_date
            )
            VALUES (%s, TRUE, %s, %s, %s, 'sendgrid', %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (normalized_email) DO UPDATE SET
                active = TRUE,
                deactivated_at = NULL,
                reason = CASE
                    WHEN bhsoft_email_suppression.active IS NOT TRUE
                      OR bhsoft_email_suppression.last_event_at IS NULL
                      OR EXCLUDED.last_event_at >= bhsoft_email_suppression.last_event_at
                    THEN EXCLUDED.reason
                    ELSE bhsoft_email_suppression.reason
                END,
                first_event_at = LEAST(
                    COALESCE(bhsoft_email_suppression.first_event_at, EXCLUDED.first_event_at),
                    EXCLUDED.first_event_at
                ),
                last_event_at = GREATEST(
                    COALESCE(bhsoft_email_suppression.last_event_at, EXCLUDED.last_event_at),
                    EXCLUDED.last_event_at
                ),
                provider = CASE
                    WHEN bhsoft_email_suppression.active IS NOT TRUE
                      OR bhsoft_email_suppression.last_event_at IS NULL
                      OR EXCLUDED.last_event_at >= bhsoft_email_suppression.last_event_at
                    THEN EXCLUDED.provider
                    ELSE bhsoft_email_suppression.provider
                END,
                last_provider_event_id = CASE
                    WHEN bhsoft_email_suppression.active IS NOT TRUE
                      OR bhsoft_email_suppression.last_event_at IS NULL
                      OR EXCLUDED.last_event_at >= bhsoft_email_suppression.last_event_at
                    THEN EXCLUDED.last_provider_event_id
                    ELSE bhsoft_email_suppression.last_provider_event_id
                END,
                last_detail = CASE
                    WHEN bhsoft_email_suppression.active IS NOT TRUE
                      OR bhsoft_email_suppression.last_event_at IS NULL
                      OR EXCLUDED.last_event_at >= bhsoft_email_suppression.last_event_at
                    THEN EXCLUDED.last_detail
                    ELSE bhsoft_email_suppression.last_detail
                END,
                write_uid = EXCLUDED.write_uid,
                write_date = NOW()
            RETURNING id
        """, (
            normalized_email,
            reason,
            event_time,
            event_time,
            provider_event_id,
            detail,
            self.env.uid,
            self.env.uid,
        ))
        suppression = self.browse(self.env.cr.fetchone()[0])
        suppression.invalidate_recordset(flush=False)
        suppression._enforce_on_pending_queues()
        return suppression

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals['normalized_email'] = self._normalize_email(vals.get('normalized_email'))
            if not vals['normalized_email']:
                raise ValidationError(_('Email is required.'))
            vals.setdefault('provider', 'manual')
            vals.setdefault('first_event_at', fields.Datetime.now())
            vals.setdefault('last_event_at', vals['first_event_at'])
        records = super().create(vals_list)
        records.filtered('active')._enforce_on_pending_queues()
        return records

    def unlink(self):
        if self.env['bhsoft.mail.queue'].sudo().search_count([
            ('suppression_id', 'in', self.ids),
        ]):
            raise ValidationError(_(
                'A suppression referenced by email queues cannot be deleted. Deactivate it instead.'
            ))
        return super().unlink()

    def write(self, vals):
        if 'normalized_email' in vals:
            vals['normalized_email'] = self._normalize_email(vals.get('normalized_email'))
            if not vals['normalized_email']:
                raise ValidationError(_('Email is required.'))
            changed = self.filtered(
                lambda record: record.normalized_email != vals['normalized_email']
            )
            if len(changed) > 1:
                raise ValidationError(_(
                    'Change one suppression email address at a time.'
                ))
            if changed and self.env['bhsoft.mail.queue'].sudo().search_count([
                ('suppression_id', 'in', changed.ids),
            ]):
                raise ValidationError(_(
                    'The email address cannot be changed after queues reference this suppression. '
                    'Deactivate it and create a new suppression instead.'
                ))
        if vals.get('active') is False:
            vals.setdefault('deactivated_at', fields.Datetime.now())
        elif vals.get('active'):
            vals.setdefault('deactivated_at', False)
        result = super().write(vals)
        active = self.filtered('active')
        if vals.get('active') or ('normalized_email' in vals and active):
            active._enforce_on_pending_queues()
        return result

    def _enforce_on_pending_queues(self):
        active = self.filtered('active')
        if not active:
            return
        by_email = {record.normalized_email: record for record in active}
        queues = self.env['bhsoft.mail.queue'].sudo().search([
            ('normalized_email', 'in', list(by_email)),
            ('status', 'in', ('none', 'processing', 'queued', 'unknown')),
        ])
        for queue in queues:
            suppression = by_email.get(queue.normalized_email)
            vals = {'suppression_id': suppression.id}
            if queue.status in ('none', 'processing'):
                vals.update({
                    'status': 'skipped',
                    'terminal_at': queue.terminal_at or fields.Datetime.now(),
                    'error_message': _(
                        'Skipped because this recipient is suppressed: %(reason)s',
                        reason=suppression.reason,
                    ),
                })
            queue.write(vals)

        cron = self.env.ref(
            'bhs_sendgrid_mailer.ir_cron_enforce_email_suppressions',
            raise_if_not_found=False,
        )
        if cron and queues.filtered(lambda queue: queue.status == 'queued'):
            cron.sudo()._trigger()
