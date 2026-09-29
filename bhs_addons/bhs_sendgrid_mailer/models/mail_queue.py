from odoo import models, fields, api, _, tools
import random
import requests
import re
import uuid
from datetime import timedelta, datetime, timezone
import calendar


MAIL_BATCH_LOCK_NAMESPACE = 4_273_052
MAIL_BATCH_LOCK_KEY = 1
TERMINAL_STATUSES = (
    'sent', 'delivered', 'opened', 'clicked', 'failed', 'bounced', 'dropped',
    'spamreport', 'unsubscribed', 'cancelled', 'duplicate', 'skipped',
)


class BhsoftMailQueue(models.Model):
    _name = 'bhsoft.mail.queue'
    _description = 'Bulk Email Queue'
    _order = 'create_date desc'
    _inherit = ['mail.thread']

    name = fields.Char(string='Recipient', required=True)
    email_to = fields.Char(string='Recipient Email', required=True)
    normalized_email = fields.Char(string='Normalized Email', readonly=True, index=True)
    company_name = fields.Char(string='Company')
    subject = fields.Char(string='Subject', required=True)
    body_html = fields.Text(string='Email Body', required=True)

    status = fields.Selection([
        ('none', 'Pending'),
        ('processing', 'Processing'),
        ('queued', 'Scheduled'),
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('clicked', 'Clicked'),
        ('failed', 'Failed'),
        ('unknown', 'Provider Outcome Unknown'),
        ('bounced', 'Bounce'),
        ('dropped', 'Dropped'),
        ('spamreport', 'Spam Report'),
        ('unsubscribed', 'Unsubscribed'),
        ('cancelled', 'Cancelled'),
        ('duplicate', 'Duplicate / Skipped'),
        ('skipped', 'Skipped'),
    ], string='Status', default='none', readonly=True, index=True, tracking=True)

    source_row_uuid = fields.Char(string='Import Row UUID', readonly=True, index=True)
    source_row_number = fields.Integer(string='Excel Row', readonly=True)
    import_batch_id = fields.Char(string='Import Batch ID', readonly=True, index=True)
    duplicate_of_id = fields.Many2one('bhsoft.mail.queue', string='Duplicate Of', readonly=True, index=True)
    suppression_id = fields.Many2one(
        'bhsoft.email.suppression', string='Suppression', readonly=True,
        ondelete='restrict', index=True,
    )

    sheet_source_id = fields.Many2one(
        'bhsoft.google.sheet.source', string='Google Sheet Source', readonly=True,
        ondelete='restrict', index=True,
    )
    sheet_row_id = fields.Char(string='Odoo Row ID', readonly=True, index=True)
    sheet_row_number = fields.Integer(string='Google Sheet Row', readonly=True)
    hubspot_id = fields.Char(string='HubSpot ID', readonly=True, index=True)
    linkedin_url = fields.Char(string='LinkedIn', readonly=True)
    company_website = fields.Char(string='Company Website', readonly=True)
    source_hash = fields.Char(readonly=True, index=True)
    content_hash = fields.Char(readonly=True, index=True)
    source_synced_at = fields.Datetime(string='Source Synchronized At', readonly=True)
    source_missing = fields.Boolean(string='Missing From Source', readonly=True, index=True)
    source_conflict = fields.Boolean(string='Source Conflict', readonly=True, index=True)
    source_conflict_message = fields.Text(readonly=True)
    sync_run_id = fields.Many2one(
        'bhsoft.google.sheet.sync.run', string='Last Sync Run', readonly=True,
        ondelete='set null', index=True,
    )

    _sheet_row_unique = models.UniqueIndex(
        '(sheet_source_id, sheet_row_id) WHERE sheet_source_id IS NOT NULL AND sheet_row_id IS NOT NULL',
        'Odoo Row ID must be unique within each Google Sheet source.',
    )

    scheduled_send_time = fields.Datetime(string='Scheduled Send Time', readonly=True)
    actual_sent_time = fields.Datetime(string='Actual Send Time', readonly=True)
    error_message = fields.Text(string='Error Details', readonly=True)
    webhook_log = fields.Text(string='Webhook History', readonly=True, help='Stores update history received from the SendGrid webhook')
    api_sync_log = fields.Text(string='API Sync History', readonly=True, help='Stores status synchronization history from the SendGrid API')

    mail_id = fields.Many2one('mail.mail', string='Original Odoo Mail', readonly=True, help='Original mail record managed by Odoo')
    sendgrid_msg_id = fields.Char(string='SendGrid Message ID', readonly=True, index=True, help='ID returned by the SendGrid API for status lookup')
    sendgrid_batch_id = fields.Char(string='SendGrid Batch ID', readonly=True, index=True, help='Batch ID used to cancel a scheduled SendGrid message')
    cancelled_at = fields.Datetime(string='Cancelled At', readonly=True)
    cancelled_by = fields.Many2one('res.users', string='Cancelled By', readonly=True)
    cancel_error = fields.Text(string='Cancellation Error', readonly=True)
    tracking_mode = fields.Selection([
        ('off', 'Off'),
        ('webhook', 'Webhook'),
        ('api', 'API Polling'),
        ('both', 'Webhook + API'),
    ], string='Tracking Mode', readonly=True, default='off')
    sendgrid_status = fields.Char(string='SendGrid Status', readonly=True)
    last_sendgrid_event = fields.Char(string='Last SendGrid Event', readonly=True, index=True)
    last_sendgrid_event_time = fields.Datetime(string='Last Event Time', readonly=True)
    last_sendgrid_sync_time = fields.Datetime(string='Last API Sync', readonly=True)
    terminal_at = fields.Datetime(string='Terminal At', readonly=True, index=True)
    compacted_at = fields.Datetime(string='Compacted At', readonly=True, index=True)
    event_ids = fields.One2many(
        'bhsoft.mail.event', 'queue_id', string='SendGrid Events', readonly=True,
    )

    @api.model
    def _normalize_email(self, email):
        return (email or '').strip().lower()

    @api.model
    def _is_valid_email(self, email):
        email_regex = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
        return bool(email and email_regex.match(email.strip()))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            email = vals.get('email_to', '')
            vals.setdefault('source_row_uuid', str(uuid.uuid4()))
            vals['normalized_email'] = self._normalize_email(email)
            if email and not self._is_valid_email(email):
                vals['status'] = 'failed'
                vals['error_message'] = _('Invalid email format (Spam/Invalid)')
            if vals.get('status') in TERMINAL_STATUSES and 'terminal_at' not in vals:
                vals['terminal_at'] = fields.Datetime.now()
        return super().create(vals_list)

    def write(self, vals):
        if 'email_to' in vals:
            email = vals.get('email_to', '')
            vals['normalized_email'] = self._normalize_email(email)
            if email and not self._is_valid_email(email):
                vals['status'] = 'failed'
                vals['error_message'] = _('Invalid email format (Spam/Invalid)')
        if vals.get('status') in TERMINAL_STATUSES and 'terminal_at' not in vals:
            vals['terminal_at'] = fields.Datetime.now()
        elif vals.get('status') in ('none', 'processing', 'queued'):
            vals.setdefault('terminal_at', False)
        return super().write(vals)

    def action_retry(self):
        """Allow retry only for technical sending failures."""
        for record in self.filtered(lambda r: r.status == 'failed'):
            record.write({
                'status': 'none',
                'error_message': False,
                'scheduled_send_time': False,
                'actual_sent_time': False,
                'mail_id': False,
                'sendgrid_msg_id': False,
                'sendgrid_batch_id': False,
                'sendgrid_status': False,
                'last_sendgrid_event': False,
                'last_sendgrid_event_time': False,
                'last_sendgrid_sync_time': False,
                'cancelled_at': False,
                'cancelled_by': False,
                'cancel_error': False,
            })

    @api.model
    def _sendgrid_headers(self, api_key):
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

    @api.model
    def _create_sendgrid_batch_id(self, api_key):
        response = requests.post(
            'https://api.sendgrid.com/v3/mail/batch',
            headers=self._sendgrid_headers(api_key),
            timeout=15,
        )
        if response.status_code not in (200, 201):
            raise Exception(_(
                'SendGrid Batch API error %(status)s: %(response)s',
                status=response.status_code,
                response=response.text,
            ))
        batch_id = response.json().get('batch_id')
        if not batch_id:
            raise Exception(_('The SendGrid Batch API did not return a Batch ID.'))
        return batch_id

    @api.model
    def _sendgrid_cancel_status_exists(self, response):
        if response.status_code != 400:
            return False
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        errors = payload.get('errors', [])
        return any(
            'status for this batch id exists' in str(error.get('message', '')).lower()
            and 'patch' in str(error.get('message', '')).lower()
            for error in errors
            if isinstance(error, dict)
        )

    @api.model
    def _cancel_sendgrid_batch(self, api_key, batch_id):
        url = 'https://api.sendgrid.com/v3/user/scheduled_sends'
        headers = self._sendgrid_headers(api_key)
        response = requests.post(
            url,
            json={'batch_id': batch_id, 'status': 'cancel'},
            headers=headers,
            timeout=15,
        )
        if self._sendgrid_cancel_status_exists(response):
            response = requests.patch(
                f'{url}/{batch_id}',
                json={'status': 'cancel'},
                headers=headers,
                timeout=15,
            )
        if response.status_code not in (200, 201, 202, 204):
            raise Exception(_(
                'SendGrid Cancel API error %(status)s: %(response)s',
                status=response.status_code,
                response=response.text,
            ))

    def action_cancel_scheduled(self):
        api_key = self.env['ir.config_parameter'].sudo().get_str('bhs_sendgrid_mailer.api_key', '').strip()
        now = fields.Datetime.now()
        cancelled = self.browse()
        skipped = self.browse()
        failed = self.browse()

        for record in self:
            if record.status == 'none':
                record.write({
                    'status': 'cancelled',
                    'cancelled_at': now,
                    'cancelled_by': self.env.user.id,
                    'cancel_error': False,
                })
                cancelled |= record
                continue

            if record.status != 'queued':
                skipped |= record
                continue

            if record.scheduled_send_time and now >= record.scheduled_send_time:
                skipped |= record
                continue

            try:
                if record.sendgrid_batch_id:
                    if not api_key:
                        raise Exception(_('SendGrid API Key is not configured.'))
                    self._cancel_sendgrid_batch(api_key, record.sendgrid_batch_id)
                elif record.sendgrid_msg_id:
                    raise Exception(_('This scheduled message was created without a SendGrid Batch ID and cannot be cancelled remotely.'))
                elif record.mail_id:
                    record.mail_id.sudo().write({'state': 'cancel'})

                record.write({
                    'status': 'cancelled',
                    'sendgrid_status': 'cancelled',
                    'last_sendgrid_event': 'cancelled',
                    'last_sendgrid_event_time': now,
                    'cancelled_at': now,
                    'cancelled_by': self.env.user.id,
                    'cancel_error': False,
                })
                cancelled |= record
            except Exception as error:
                record.write({'cancel_error': str(error)})
                failed |= record

        message = _(
            'Cancelled: %(cancelled)s. Already sent or not cancellable: %(skipped)s. Failed: %(failed)s.',
            cancelled=len(cancelled),
            skipped=len(skipped),
            failed=len(failed),
        )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Cancel Scheduled Emails'),
                'message': message,
                'type': 'warning' if failed or skipped else 'success',
                'sticky': bool(failed),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_sync_status(self):
        """Synchronize scheduled SendGrid messages and Odoo mail records."""
        current_time = fields.Datetime.now()
        for record in self:
            if record.scheduled_send_time and record.status in ('queued', 'processing'):
                sendgrid_submission = bool(record.sendgrid_msg_id or record.sendgrid_batch_id)
                odoo_submission = record.mail_id and record.mail_id.state == 'sent'
                if (sendgrid_submission or odoo_submission) and current_time >= record.scheduled_send_time:
                    record.write({
                        'status': 'sent',
                        'actual_sent_time': record.scheduled_send_time,
                        'terminal_at': record.terminal_at or current_time,
                    })
                    continue

            if record.mail_id and record.mail_id.state in ('sent', 'exception') and record.status in ('queued', 'processing'):
                if record.mail_id.state == 'sent' and current_time >= (record.scheduled_send_time or current_time):
                    record.write({
                        'status': 'sent',
                        'actual_sent_time': current_time,
                        'terminal_at': record.terminal_at or current_time,
                    })
                elif record.mail_id.state == 'exception':
                    record.write({
                        'status': 'failed',
                        'terminal_at': record.terminal_at or current_time,
                        'error_message': record.mail_id.failure_reason or _('Failed to send through Odoo SMTP'),
                    })

    @api.model
    def _is_unknown_provider_error(self, error):
        return isinstance(error, (requests.Timeout, requests.ConnectionError))

    def _send_via_sendgrid_api(self, record, api_key, sender_email, scheduled_date=None, batch_id=None):
        url = "https://api.sendgrid.com/v3/mail/send"
        headers = self._sendgrid_headers(api_key)
        personalization = {
            "to": [{"email": record.email_to, "name": record.name}],
            "subject": record.subject,
            "custom_args": {
                "queue_id": str(record.id),
                "source_row_uuid": record.source_row_uuid or '',
                "import_batch_id": record.import_batch_id or '',
            }
        }
        data = {
            "personalizations": [personalization],
            "from": {"email": sender_email or "no-reply@bhsoft.com"},
            "content": [{"type": "text/plain", "value": record.body_html}]
        }
        if scheduled_date:
            data["send_at"] = calendar.timegm(scheduled_date.timetuple())
        if batch_id:
            data['batch_id'] = batch_id

        response = requests.post(url, json=data, headers=headers, timeout=15)
        if response.status_code not in (200, 202):
            raise Exception(_(
                'SendGrid API error %(status)s: %(response)s',
                status=response.status_code,
                response=response.text,
            ))
        return response.headers.get('X-Message-Id', '')

    def _process_send_records(self, records, schedule_cursor=None):
        records = records.filtered(
            lambda r: r.status == 'processing' and not r.duplicate_of_id and not r.source_conflict
        )
        if not records:
            return

        config = self.env['ir.config_parameter'].sudo()
        api_key = config.get_str('bhs_sendgrid_mailer.api_key', '').strip()
        sender_email = config.get_str('bhs_sendgrid_mailer.sender_email', '').strip()
        delay_min = config.get_int('bhs_sendgrid_mailer.delay_min', 45)
        delay_max = config.get_int('bhs_sendgrid_mailer.delay_max', 90)
        tracking_mode = config.get_str('bhs_sendgrid_mailer.tracking_mode', 'off')

        if delay_min < 0 or delay_max < delay_min:
            message = _(
                'Pre-send error: the delay range is invalid. Minimum Delay must be zero or greater and cannot exceed Maximum Delay.'
            )
            records.write({'status': 'failed', 'error_message': message})
            return
        if api_key and not sender_email:
            records.write({
                'status': 'failed',
                'error_message': _('Pre-send error: Sender Email is required when using SendGrid.'),
            })
            return

        current_time = max(
            fields.Datetime.now(),
            schedule_cursor or fields.Datetime.now(),
        )

        Suppression = self.env['bhsoft.email.suppression'].sudo()
        for record in records:
            email = record.email_to and record.email_to.strip() or ''
            if not self._is_valid_email(email):
                record.write({
                    'status': 'failed',
                    'terminal_at': fields.Datetime.now(),
                    'error_message': _('Pre-send error: Invalid email format (Spam/Invalid)')
                })
                continue

            suppression = Suppression.active_for_email(record.normalized_email or email)
            if suppression:
                record.write({
                    'status': 'skipped',
                    'suppression_id': suppression.id,
                    'terminal_at': fields.Datetime.now(),
                    'error_message': _(
                        'Skipped because this recipient is suppressed: %(reason)s',
                        reason=suppression.reason,
                    ),
                })
                continue

            record.write({
                'error_message': False,
                'tracking_mode': tracking_mode,
            })

            delay_seconds = random.randint(delay_min, delay_max)
            scheduled_date = current_time + timedelta(seconds=delay_seconds)

            batch_id = False
            try:
                mail_values = {
                    'subject': record.subject,
                    'body_html': tools.plaintext2html(record.body_html),
                    'email_to': record.email_to,
                    'scheduled_date': scheduled_date,
                    'auto_delete': False,
                }
                if sender_email:
                    mail_values['email_from'] = sender_email

                if api_key:
                    batch_id = self._create_sendgrid_batch_id(api_key)
                    msg_id = self._send_via_sendgrid_api(record, api_key, sender_email, scheduled_date, batch_id)

                    record.write({
                        'status': 'queued',
                        'scheduled_send_time': scheduled_date,
                        'mail_id': False,
                        'sendgrid_msg_id': msg_id,
                        'sendgrid_batch_id': batch_id,
                        'sendgrid_status': 'queued',
                        'last_sendgrid_event': 'queued',
                    })
                else:
                    mail_record = self.env['mail.mail'].sudo().create(mail_values)
                    record.write({
                        'status': 'queued',
                        'scheduled_send_time': scheduled_date,
                        'mail_id': mail_record.id,
                        'sendgrid_status': 'queued',
                        'last_sendgrid_event': 'queued',
                    })

                current_time = scheduled_date

            except Exception as e:
                if api_key and self._is_unknown_provider_error(e):
                    record.write({
                        'status': 'unknown',
                        'scheduled_send_time': scheduled_date,
                        'sendgrid_batch_id': batch_id or False,
                        'sendgrid_status': 'unknown',
                        'error_message': _(
                            'The SendGrid request outcome is unknown because the connection timed out or failed. '
                            'Do not retry until the provider activity has been reconciled.'
                        ),
                    })
                    current_time = scheduled_date
                else:
                    record.write({
                        'status': 'failed',
                        'error_message': str(e),
                    })

    @api.model
    def _acquire_mail_batch_lock(self):
        self.env.cr.execute(
            'SELECT pg_try_advisory_xact_lock(%s, %s)',
            (MAIL_BATCH_LOCK_NAMESPACE, MAIL_BATCH_LOCK_KEY),
        )
        return bool(self.env.cr.fetchone()[0])

    @api.model
    def _latest_schedule_cursor(self):
        self.env.cr.execute("""
            SELECT MAX(scheduled_send_time)
              FROM bhsoft_mail_queue
             WHERE status IN ('processing', 'queued', 'unknown')
               AND scheduled_send_time IS NOT NULL
        """)
        return self.env.cr.fetchone()[0]

    @api.model
    def _dispatch_mail_batch(self, record_ids=None):
        config = self.env['ir.config_parameter'].sudo()
        batch_size = config.get_int('bhs_sendgrid_mailer.batch_size', 30)
        result = {
            'records': self.browse(),
            'reason': False,
            'active_until': False,
        }
        if batch_size <= 0:
            result['reason'] = 'disabled'
            return result
        if not self._acquire_mail_batch_lock():
            result['reason'] = 'busy'
            return result

        schedule_cursor = self._latest_schedule_cursor()
        records = self._claim_pending_records(
            limit=batch_size,
            record_ids=record_ids,
        )
        self._process_send_records(records, schedule_cursor=schedule_cursor)
        result['records'] = records
        result['active_until'] = schedule_cursor
        return result

    def action_send_now(self):
        """Submit at most one configured batch without overlapping its schedule."""
        requested_ids = self.ids
        eligible_count = self.search_count([
            ('id', 'in', requested_ids),
            ('status', '=', 'none'),
            ('duplicate_of_id', '=', False),
            ('source_conflict', '!=', True),
        ])
        result = self._dispatch_mail_batch(record_ids=requested_ids)
        processed = result['records']
        submitted_count = len(processed.filtered(
            lambda record: record.status in ('queued', 'unknown')
        ))
        claim_skipped_count = self.search_count([
            ('id', 'in', requested_ids),
            ('status', '=', 'skipped'),
            ('suppression_id.active', '=', True),
        ])
        processed_unsent_count = len(processed) - submitted_count
        deferred_count = max(
            eligible_count - len(processed) - claim_skipped_count, 0,
        )
        skipped_count = len(self) - eligible_count + processed_unsent_count

        if result['reason'] == 'busy':
            message = _(
                'Another batch is being prepared. %(pending)s selected messages remain Pending.',
                pending=eligible_count,
            )
        elif result['reason'] == 'disabled':
            message = _('Batch Size must be greater than zero. No messages were submitted.')
        else:
            message = _(
                'Submitted: %(submitted)s. Deferred to a later batch: %(deferred)s. '
                'Already processed or not sendable: %(skipped)s.',
                submitted=submitted_count,
                deferred=deferred_count,
                skipped=skipped_count,
            )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Send Emails'),
                'message': message,
                'type': 'warning' if result['reason'] or deferred_count or skipped_count else 'success',
                'sticky': bool(result['reason']),
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    @api.model
    def _claim_pending_records(self, limit=None, record_ids=None):
        config = self.env['ir.config_parameter'].sudo()
        limit = limit or config.get_int('bhs_sendgrid_mailer.batch_size', 30)
        if limit <= 0:
            return self.browse()

        self.env.cr.execute("""
            UPDATE bhsoft_mail_queue AS queue
               SET status = 'skipped',
                   suppression_id = suppression.id,
                   terminal_at = COALESCE(queue.terminal_at, NOW()),
                   error_message = %s,
                   write_date = NOW(),
                   write_uid = %s
              FROM bhsoft_email_suppression AS suppression
             WHERE queue.status = 'none'
               AND suppression.active IS TRUE
               AND suppression.normalized_email = queue.normalized_email
         RETURNING queue.id
        """, [
            _('Skipped because this recipient is suppressed.'),
            self.env.uid,
        ])
        suppressed_ids = [row[0] for row in self.env.cr.fetchall()]
        if suppressed_ids:
            self.browse(suppressed_ids).invalidate_recordset([
                'status', 'suppression_id', 'terminal_at', 'error_message',
                'write_date', 'write_uid',
            ], flush=False)

        id_filter = ''
        params = [limit]
        if record_ids is not None:
            if not record_ids:
                return self.browse()
            id_filter = 'AND id = ANY(%s)'
            params.insert(0, list(record_ids))

        self.env.cr.execute(f"""
            SELECT id
              FROM bhsoft_mail_queue
             WHERE status = 'none'
               AND duplicate_of_id IS NULL
               AND source_conflict IS NOT TRUE
               {id_filter}
             ORDER BY id
             FOR UPDATE SKIP LOCKED
             LIMIT %s
        """, params)
        claimed_ids = [row[0] for row in self.env.cr.fetchall()]
        if not claimed_ids:
            return self.browse()
        self.env.cr.execute("""
            UPDATE bhsoft_mail_queue
               SET status = 'processing', write_date = NOW(), write_uid = %s
             WHERE id = ANY(%s) AND status = 'none'
         RETURNING id
        """, [self.env.uid, claimed_ids])
        claimed_ids = [row[0] for row in self.env.cr.fetchall()]
        claimed = self.browse(claimed_ids).exists()
        claimed.invalidate_recordset(['status', 'write_date', 'write_uid'])
        return claimed

    @api.model
    def _cron_auto_sync_status(self):
        """Tự động đồng bộ trạng thái từ mail.mail sang bhsoft.mail.queue."""
        records = self.search([('status', 'in', ('queued', 'processing'))])
        records.action_sync_status()

    @api.model
    def _cron_process_mail_queue(self):
        """Submit one batch only when the previous batch schedule has completed."""
        self._dispatch_mail_batch()

    def _sendgrid_event_datetime(self, event_data):
        raw_timestamp = event_data.get('timestamp') or event_data.get('processed')
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
        return False

    def _status_from_sendgrid_event(self, event_type):
        mapping = {
            'processed': 'queued',
            'delivered': 'delivered',
            'open': 'opened',
            'click': 'clicked',
            'bounce': 'bounced',
            'dropped': 'dropped',
            'spamreport': 'spamreport',
            'unsubscribe': 'unsubscribed',
            'group_unsubscribe': 'unsubscribed',
            'deferred': 'queued',
        }
        return mapping.get(event_type, self.status)

    def _apply_sendgrid_event(self, event_type, event_data, source='webhook'):
        event_type = (event_type or '').lower()
        event_time = self._sendgrid_event_datetime(event_data or {})
        reason = (event_data or {}).get('reason') or (event_data or {}).get('response') or ''
        state_rank = {
            'none': 0, 'processing': 1, 'queued': 2, 'sent': 3,
            'delivered': 4, 'opened': 5, 'clicked': 6,
        }
        for record in self:
            event_recorded = self.env['bhsoft.mail.event'].sudo().record_once(
                record, event_type, event_data or {}, source, event_time,
            )
            suppression = self.env['bhsoft.email.suppression'].sudo().browse()
            if event_recorded:
                suppression = self.env['bhsoft.email.suppression'].sudo().upsert_from_sendgrid_event(
                    (event_data or {}).get('email') or record.normalized_email or record.email_to,
                    event_type,
                    event_data or {},
                )
                if suppression and record.suppression_id != suppression:
                    record.write({'suppression_id': suppression.id})
            if not event_recorded:
                continue
            status = record._status_from_sendgrid_event(event_type)
            vals = {}
            is_newer = not record.last_sendgrid_event_time or not event_time or event_time >= record.last_sendgrid_event_time
            if is_newer:
                vals.update({
                    'sendgrid_status': event_type,
                    'last_sendgrid_event': event_type,
                })
            terminal_failure = event_type in (
                'bounce', 'dropped', 'spamreport', 'unsubscribe', 'group_unsubscribe',
            )
            if event_time and is_newer:
                vals['last_sendgrid_event_time'] = event_time
            preserve_terminal_failure = record.status in (
                'failed', 'bounced', 'dropped', 'spamreport', 'unsubscribed',
                'cancelled', 'skipped',
            ) and (
                not terminal_failure or record.status in ('cancelled', 'skipped')
            )
            if status and not preserve_terminal_failure and (
                terminal_failure
                or state_rank.get(status, -1) >= state_rank.get(record.status, -1)
            ) and is_newer:
                vals['status'] = status
                if status in ('processing', 'queued'):
                    vals['terminal_at'] = False
            if event_type == 'delivered' and event_time and not preserve_terminal_failure:
                vals['actual_sent_time'] = event_time
            if event_type in (
                'bounce', 'dropped', 'spamreport', 'unsubscribe', 'group_unsubscribe',
            ) and not preserve_terminal_failure:
                if reason:
                    vals['error_message'] = _(
                        'SendGrid event: %(event)s\nReason: %(reason)s',
                        event=event_type.upper(),
                        reason=reason,
                    )
                else:
                    vals['error_message'] = _(
                        'SendGrid event: %(event)s',
                        event=event_type.upper(),
                    )
            if status in (
                'delivered', 'opened', 'clicked', 'failed', 'bounced', 'dropped',
                'spamreport', 'unsubscribed', 'cancelled', 'skipped',
            ) and is_newer:
                vals['terminal_at'] = record.terminal_at or event_time or fields.Datetime.now()
            if source == 'api':
                vals['last_sendgrid_sync_time'] = fields.Datetime.now()

            record.write(vals)

    def action_sync_sendgrid_activity(self):
        self._sync_sendgrid_activity_records(self)

    @api.model
    def _cron_sync_sendgrid_activity(self):
        config = self.env['ir.config_parameter'].sudo()
        tracking_mode = config.get_str('bhs_sendgrid_mailer.tracking_mode', 'off')
        polling_active = config.get_bool('bhs_sendgrid_mailer.api_polling_active', False)
        if tracking_mode not in ('api', 'both') or not polling_active:
            return

        window_days = config.get_int('bhs_sendgrid_mailer.api_polling_window_days', 7)
        min_create_date = fields.Datetime.now() - timedelta(days=window_days)
        records = self.search([
            ('status', 'in', ('processing', 'queued', 'sent', 'delivered', 'opened', 'clicked')),
            ('sendgrid_msg_id', '!=', False),
            ('tracking_mode', 'in', ('api', 'both')),
            ('create_date', '>=', min_create_date),
        ], limit=100)
        self._sync_sendgrid_activity_records(records)

    def _sync_sendgrid_activity_records(self, records):
        config = self.env['ir.config_parameter'].sudo()
        api_key = config.get_str('bhs_sendgrid_mailer.api_key', '').strip()
        if not api_key:
            records._append_api_sync_log(
                _('The SendGrid API Key is not configured for API status synchronization.')
            )
            return

        headers = {"Authorization": f"Bearer {api_key}"}
        for record in records.filtered(lambda r: r.sendgrid_msg_id):
            base_msg_id = record.sendgrid_msg_id.split('.')[0]
            query = f'msg_id LIKE "{base_msg_id}%"'
            try:
                response = requests.get(
                    'https://api.sendgrid.com/v3/messages',
                    params={'query': query, 'limit': 10},
                    headers=headers,
                    timeout=15,
                )
                record.write({'last_sendgrid_sync_time': fields.Datetime.now()})
                if response.status_code == 404:
                    record._append_api_sync_log(
                        _('The message was not found in the SendGrid Activity API.')
                    )
                    continue
                if response.status_code in (401, 403):
                    record._append_api_sync_log(_(
                        'The SendGrid Activity API is inaccessible or unsupported by the account plan (HTTP %(status)s).',
                        status=response.status_code,
                    ))
                    continue
                if response.status_code not in (200, 202):
                    record._append_api_sync_log(_(
                        'SendGrid Activity API error %(status)s: %(response)s',
                        status=response.status_code,
                        response=response.text,
                    ))
                    continue

                data = response.json()
                messages = data.get('messages') if isinstance(data, dict) else data
                if isinstance(messages, dict):
                    messages = [messages]
                if not messages:
                    record._append_api_sync_log(
                        _('The SendGrid Activity API has no new events for this message.')
                    )
                    continue

                latest_event = None
                latest_event_time = None
                for message in messages:
                    events = message.get('events') or []
                    if not events and message.get('status'):
                        events = [{'event_name': message.get('status'), 'processed': message.get('last_event_time') or message.get('created_at')}]
                    for event in events:
                        event_type = event.get('event_name') or event.get('event') or event.get('status')
                        event_time = record._sendgrid_event_datetime(event)
                        if event_type and event_time and (latest_event_time is None or event_time >= latest_event_time):
                            latest_event = (event_type, event)
                            latest_event_time = event_time

                if latest_event:
                    record._apply_sendgrid_event(latest_event[0], latest_event[1], source='api')
                else:
                    record._append_api_sync_log(
                        _('The SendGrid Activity API returned data without a valid event.')
                    )

            except Exception as error:
                record._append_api_sync_log(_(
                    'Error while calling the SendGrid Activity API: %(error)s',
                    error=str(error),
                ))

    def _append_api_sync_log(self, message):
        now = fields.Datetime.to_string(fields.Datetime.now())
        for record in self:
            current_log = record.api_sync_log or ''
            log_line = _('[%(time)s] %(message)s', time=now, message=message)
            record.write({
                'api_sync_log': '\n'.join((current_log.splitlines() + [log_line])[-200:]),
                'last_sendgrid_sync_time': fields.Datetime.now(),
            })

    @api.model
    def _cron_enforce_email_suppressions(self):
        config = self.env['ir.config_parameter'].sudo()
        api_key = config.get_str('bhs_sendgrid_mailer.api_key', '').strip()
        now = fields.Datetime.now()
        records = self.search([
            ('status', 'in', ('queued', 'unknown')),
            ('suppression_id.active', '=', True),
            '|',
            ('scheduled_send_time', '>', now),
            '&',
            ('mail_id', '!=', False),
            ('mail_id.state', 'not in', ('sent', 'cancel', 'exception')),
        ], order='id asc', limit=500)
        for record in records:
            try:
                if record.sendgrid_batch_id:
                    if not api_key:
                        raise Exception(_('SendGrid API Key is not configured.'))
                    record._cancel_sendgrid_batch(api_key, record.sendgrid_batch_id)
                elif record.mail_id:
                    record.mail_id.sudo().write({'state': 'cancel'})
                else:
                    raise Exception(_(
                        'The scheduled message has no cancellable provider batch.'
                    ))
                record.write({
                    'status': 'skipped',
                    'sendgrid_status': 'cancelled',
                    'last_sendgrid_event': 'cancelled',
                    'last_sendgrid_event_time': now,
                    'cancelled_at': now,
                    'terminal_at': record.terminal_at or now,
                    'cancel_error': False,
                    'error_message': _(
                        'Cancelled because this recipient is suppressed.'
                    ),
                })
            except Exception as error:
                record.write({'cancel_error': str(error)})

    @api.autovacuum
    def _gc_mailer_data(self):
        config = self.env['ir.config_parameter'].sudo()
        queue_days = config.get_int('bhs_sendgrid_mailer.queue_retention_days', 90)
        log_days = config.get_int('bhs_sendgrid_mailer.event_retention_days', 30)
        terminal_statuses = (
            'sent', 'delivered', 'opened', 'clicked', 'failed', 'bounced',
            'dropped', 'spamreport', 'unsubscribed', 'cancelled', 'duplicate',
            'skipped',
        )
        now = fields.Datetime.now()

        if log_days > 0:
            log_deadline = now - timedelta(days=log_days)
            events = self.env['bhsoft.mail.event'].sudo().search([
                ('create_date', '<=', log_deadline),
            ], order='id asc', limit=10_000)
            event_count = len(events)
            events.with_context(prefetch_fields=False).unlink()
            logged = self.search([
                ('write_date', '<=', log_deadline),
                '|', ('webhook_log', '!=', False), ('api_sync_log', '!=', False),
            ], order='id asc', limit=10_000)
            logged_count = len(logged)
            if logged:
                logged.write({'webhook_log': False, 'api_sync_log': False})
        else:
            event_count = logged_count = 0

        if queue_days <= 0:
            return event_count + logged_count, max(
                event_count == 10_000, logged_count == 10_000,
            )
        queue_deadline = now - timedelta(days=queue_days)
        old_records = self.search([
            ('status', 'in', terminal_statuses),
            ('terminal_at', '!=', False),
            ('terminal_at', '<=', queue_deadline),
            '|', ('sheet_source_id', '=', False), ('compacted_at', '=', False),
        ], order='id asc', limit=10_000)
        sheet_records = old_records.filtered('sheet_source_id')
        removable = old_records - sheet_records

        linked_mails = old_records.mapped('mail_id').sudo()
        if linked_mails:
            old_records.write({'mail_id': False})
        if linked_mails:
            linked_mails.with_context(prefetch_fields=False).unlink()
        if sheet_records:
            sheet_records.write({
                'name': _('Archived recipient'),
                'company_name': False,
                'subject': _('[Archived email subject]'),
                'body_html': _('[Archived email body]'),
                'hubspot_id': False,
                'linkedin_url': False,
                'company_website': False,
                'source_conflict_message': False,
                'webhook_log': False,
                'api_sync_log': False,
                'mail_id': False,
                'compacted_at': now,
            })
        if removable:
            removable.with_context(prefetch_fields=False).unlink()
        return event_count + logged_count + len(old_records), max(
            event_count == 10_000,
            logged_count == 10_000,
            len(old_records) == 10_000,
        )
