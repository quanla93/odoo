import json

import requests

from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

from ..services.google_sheets_client import GoogleSheetsClient


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sendgrid_api_key = fields.Char(
        string='New SendGrid API Key',
        groups='base.group_system',
    )
    sendgrid_sender = fields.Char(string='Sender Email', config_parameter='bhs_sendgrid_mailer.sender_email')
    sendgrid_api_key_configured = fields.Boolean(
        string='SendGrid API Key Configured',
        compute='_compute_credential_status',
    )
    sendgrid_webhook_public_key = fields.Text(
        string='New SendGrid Webhook Verification Key',
        groups='base.group_system',
    )
    sendgrid_webhook_public_key_configured = fields.Boolean(
        string='Webhook Verification Key Configured',
        compute='_compute_credential_status',
    )

    google_service_account_json = fields.Text(
        string='New Google Service Account JSON',
        groups='base.group_system',
    )
    google_credentials_configured = fields.Boolean(
        string='Google Credentials Configured',
        compute='_compute_credential_status',
    )
    google_service_account_email = fields.Char(
        string='Google Service Account Email',
        compute='_compute_credential_status',
    )
    google_project_id = fields.Char(
        string='Google Project ID',
        compute='_compute_credential_status',
    )

    sendgrid_tracking_mode = fields.Selection([
        ('off', 'Disable Tracking'),
        ('webhook', 'Webhook'),
        ('api', 'API Polling'),
        ('both', 'Webhook + API Polling'),
    ], string='Status Update Mode', config_parameter='bhs_sendgrid_mailer.tracking_mode', default='off')
    sendgrid_api_polling_active = fields.Boolean(string='Enable API Polling Job', config_parameter='bhs_sendgrid_mailer.api_polling_active')
    sendgrid_api_polling_interval = fields.Integer(string='API Polling Interval (minutes)', config_parameter='bhs_sendgrid_mailer.api_polling_interval', default=15)
    sendgrid_api_polling_window_days = fields.Integer(string='Lookback Period (days)', config_parameter='bhs_sendgrid_mailer.api_polling_window_days', default=7)
    sendgrid_dedupe_mode = fields.Selection([
        ('none', 'Do not deduplicate'),
        ('batch_email', 'Deduplicate emails within each import'),
    ], string='Deduplication Mode', config_parameter='bhs_sendgrid_mailer.dedupe_mode', default='batch_email')

    sendgrid_webhook_url = fields.Char(
        string='SendGrid Webhook URL',
        compute='_compute_sendgrid_webhook_url',
        help='Copy this URL into the SendGrid Event Webhook settings'
    )
    sendgrid_last_webhook_received_at = fields.Datetime(
        string='Last Webhook Received',
        compute='_compute_sendgrid_webhook_status',
    )
    sendgrid_last_webhook_events = fields.Char(
        string='Last Webhook Events',
        compute='_compute_sendgrid_webhook_status',
    )

    mail_cron_active = fields.Boolean(string='Enable Mail Sending Job', config_parameter='bhs_sendgrid_mailer.mail_cron_active')
    mail_cron_interval = fields.Integer(string='Sending Interval (minutes)', default=30)
    mail_sync_interval = fields.Integer(string='Odoo Status Sync Interval (minutes)', default=15)
    mail_cron_batch_size = fields.Integer(string='Messages per Batch', config_parameter='bhs_sendgrid_mailer.batch_size', default=30)
    mail_cron_delay_min = fields.Integer(string='Minimum Delay (seconds)', config_parameter='bhs_sendgrid_mailer.delay_min', default=45)
    mail_cron_delay_max = fields.Integer(string='Maximum Delay (seconds)', config_parameter='bhs_sendgrid_mailer.delay_max', default=90)

    queue_retention_days = fields.Integer(
        string='Terminal Queue Retention (days)',
        config_parameter='bhs_sendgrid_mailer.queue_retention_days', default=90,
    )
    event_retention_days = fields.Integer(
        string='Event and Log Retention (days)',
        config_parameter='bhs_sendgrid_mailer.event_retention_days', default=30,
    )
    sync_line_retention_days = fields.Integer(
        string='Sheet Row Audit Retention (days)',
        config_parameter='bhs_sendgrid_mailer.sync_line_retention_days', default=30,
    )
    sync_run_retention_days = fields.Integer(
        string='Sheet Run Summary Retention (days)',
        config_parameter='bhs_sendgrid_mailer.sync_run_retention_days', default=365,
    )

    @api.model
    def _get_stored_credential(self, parameter):
        return self.env['ir.config_parameter'].sudo().get_str(parameter, '')

    @api.model
    def _credential_value(self, new_value, parameter):
        return (new_value or '').strip() or self._get_stored_credential(parameter).strip()

    @api.depends_context('uid')
    def _compute_credential_status(self):
        api_key = self._get_stored_credential('bhs_sendgrid_mailer.api_key')
        google_json = self._get_stored_credential(
            'bhs_sendgrid_mailer.google_service_account_json'
        )
        webhook_key = self._get_stored_credential(
            'bhs_sendgrid_mailer.webhook_public_key'
        )
        try:
            google_info = json.loads(google_json) if google_json else {}
        except (TypeError, ValueError):
            google_info = {}
        for record in self:
            record.sendgrid_api_key_configured = bool(api_key.strip())
            record.google_credentials_configured = bool(google_json.strip())
            record.google_service_account_email = google_info.get('client_email') or False
            record.google_project_id = google_info.get('project_id') or False
            record.sendgrid_webhook_public_key_configured = bool(webhook_key.strip())

    def action_test_sendgrid_credentials(self):
        self.ensure_one()
        api_key = self._credential_value(
            self.sendgrid_api_key,
            'bhs_sendgrid_mailer.api_key',
        )
        if not api_key:
            raise ValidationError(_('Enter a SendGrid API Key before testing the connection.'))
        try:
            response = requests.get(
                'https://api.sendgrid.com/v3/user/profile',
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=15,
            )
        except requests.RequestException as error:
            raise UserError(_(
                'Unable to connect to SendGrid: %(error)s', error=error
            )) from error
        if response.status_code != 200:
            raise ValidationError(_(
                'SendGrid rejected the API Key (HTTP %(status)s).',
                status=response.status_code,
            ))
        try:
            profile = response.json()
        except requests.JSONDecodeError as error:
            raise UserError(_('SendGrid returned an invalid response.')) from error
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('SendGrid Connection'),
                'message': _(
                    'Connected to SendGrid as %(account)s.',
                    account=profile.get('email') or profile.get('username') or _('Unknown'),
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_clear_sendgrid_credentials(self):
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.api_key', ''
        )
        self.sendgrid_api_key = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('SendGrid Connection'),
                'message': _('The SendGrid API Key has been cleared.'),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def action_test_google_credentials(self):
        self.ensure_one()
        credential_json = self._credential_value(
            self.google_service_account_json,
            'bhs_sendgrid_mailer.google_service_account_json',
        )
        if not credential_json:
            raise ValidationError(_(
                'Enter a Google Service Account JSON before testing the connection.'
            ))
        client = GoogleSheetsClient(credential_json)
        client.validate_credentials()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Google Service Account'),
                'message': _(
                    'Credentials are valid for %(email)s in project %(project)s.',
                    email=client.client_email,
                    project=client.project_id or _('Unknown'),
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_clear_google_credentials(self):
        self.ensure_one()
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.google_service_account_json', ''
        )
        self.google_service_account_json = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Google Service Account'),
                'message': _('The Google Service Account credential has been cleared.'),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def _compute_sendgrid_webhook_url(self):
        config = self.env['ir.config_parameter'].sudo()
        base_url = config.get_str('web.base.url', 'http://localhost:8069').rstrip('/')
        for record in self:
            record.sendgrid_webhook_url = f"{base_url}/webhook/sendgrid"

    def _compute_sendgrid_webhook_status(self):
        config = self.env['ir.config_parameter'].sudo()
        received_at = config.get_str('bhs_sendgrid_mailer.last_webhook_received_at', '')
        events = config.get_str('bhs_sendgrid_mailer.last_webhook_events', '')
        for record in self:
            record.sendgrid_last_webhook_received_at = fields.Datetime.to_datetime(received_at) if received_at else False
            record.sendgrid_last_webhook_events = events or False

    def action_test_sendgrid_webhook(self):
        self.ensure_one()
        raise ValidationError(_(
            'The webhook now requires a real SendGrid signature. Use SendGrid Event Webhook settings to send a signed test event.'
        ))

    def action_clear_sendgrid_webhook_key(self):
        self.ensure_one()
        if self.sendgrid_tracking_mode in ('webhook', 'both'):
            raise ValidationError(_(
                'Disable webhook status updates before clearing the verification key.'
            ))
        self.env['ir.config_parameter'].sudo().set_str(
            'bhs_sendgrid_mailer.webhook_public_key', ''
        )
        self.sendgrid_webhook_public_key = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('SendGrid Webhook'),
                'message': _('The SendGrid Webhook Verification Key has been cleared.'),
                'type': 'success',
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }

    def set_values(self):
        if self.mail_cron_interval <= 0 or self.mail_sync_interval <= 0 or self.sendgrid_api_polling_interval <= 0:
            raise ValidationError(_('Scheduled job intervals must be greater than zero minutes.'))
        if self.mail_cron_batch_size <= 0:
            raise ValidationError(_('Messages per Batch must be greater than zero.'))
        if self.sendgrid_api_polling_window_days <= 0:
            raise ValidationError(_('The SendGrid lookback period must be greater than zero days.'))
        if any(days < 0 for days in (
            self.queue_retention_days,
            self.event_retention_days,
            self.sync_line_retention_days,
            self.sync_run_retention_days,
        )):
            raise ValidationError(_(
                'Retention periods must be zero or greater. Use zero to disable cleanup.'
            ))
        config = self.env['ir.config_parameter'].sudo()
        effective_api_key = self._credential_value(
            self.sendgrid_api_key,
            'bhs_sendgrid_mailer.api_key',
        )
        effective_webhook_key = self._credential_value(
            self.sendgrid_webhook_public_key,
            'bhs_sendgrid_mailer.webhook_public_key',
        )
        if effective_api_key and not (self.sendgrid_sender or '').strip():
            raise ValidationError(_('Sender Email is required when a SendGrid API Key is configured.'))
        if self.sendgrid_tracking_mode in ('webhook', 'both') and not effective_webhook_key:
            raise ValidationError(_(
                'The SendGrid Webhook Verification Key is required when webhook status updates are enabled.'
            ))
        if self.mail_cron_delay_min < 0 or self.mail_cron_delay_max < self.mail_cron_delay_min:
            raise ValidationError(_(
                'The delay range is invalid. Minimum Delay must be zero or greater and cannot exceed Maximum Delay.'
            ))
        if self.google_service_account_json:
            try:
                credential_info = json.loads(self.google_service_account_json)
            except (TypeError, ValueError) as error:
                raise ValidationError(_(
                    'The Google Service Account JSON is invalid: %(error)s', error=error
                )) from error
            if credential_info.get('type') != 'service_account':
                raise ValidationError(_('The Google credential must be a Service Account JSON document.'))
            if not credential_info.get('client_email') or not credential_info.get('private_key'):
                raise ValidationError(_('The Service Account JSON is missing client_email or private_key.'))
        super(ResConfigSettings, self).set_values()

        if self.sendgrid_api_key:
            config.set_str(
                'bhs_sendgrid_mailer.api_key',
                self.sendgrid_api_key.strip(),
            )
        if self.google_service_account_json:
            config.set_str(
                'bhs_sendgrid_mailer.google_service_account_json',
                self.google_service_account_json.strip(),
            )
        if self.sendgrid_webhook_public_key:
            config.set_str(
                'bhs_sendgrid_mailer.webhook_public_key',
                self.sendgrid_webhook_public_key.strip(),
            )
        config.set_bool('bhs_sendgrid_mailer.mail_cron_active', self.mail_cron_active)

        send_cron = self.env.ref('bhs_sendgrid_mailer.ir_cron_process_bhsoft_mail_queue', raise_if_not_found=False)
        if not send_cron:
            send_cron = self.env['ir.cron'].sudo().search([('name', '=', 'BHSoft: Process Outgoing Mail Queue (30 messages per batch)')], limit=1)
        if send_cron:
            send_cron.sudo().write({
                'active': self.mail_cron_active,
                'interval_number': self.mail_cron_interval,
                'interval_type': 'minutes',
            })

        mail_sync_cron = self.env.ref('bhs_sendgrid_mailer.ir_cron_sync_bhsoft_mail_queue', raise_if_not_found=False)
        if mail_sync_cron:
            mail_sync_cron.sudo().write({
                'interval_number': self.mail_sync_interval,
                'interval_type': 'minutes',
            })

        sync_cron = self.env.ref('bhs_sendgrid_mailer.ir_cron_sync_sendgrid_activity', raise_if_not_found=False)
        if sync_cron:
            sync_cron.sudo().write({
                'active': self.sendgrid_tracking_mode in ('api', 'both') and self.sendgrid_api_polling_active,
                'interval_number': self.sendgrid_api_polling_interval,
                'interval_type': 'minutes',
            })

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        config = self.env['ir.config_parameter'].sudo()
        send_cron = self.env.ref('bhs_sendgrid_mailer.ir_cron_process_bhsoft_mail_queue', raise_if_not_found=False)
        mail_sync_cron = self.env.ref('bhs_sendgrid_mailer.ir_cron_sync_bhsoft_mail_queue', raise_if_not_found=False)
        res.update(
            mail_cron_interval=send_cron.interval_number if send_cron else 30,
            mail_sync_interval=mail_sync_cron.interval_number if mail_sync_cron else 15,
            mail_cron_active=config.get_bool('bhs_sendgrid_mailer.mail_cron_active', False),
            sendgrid_tracking_mode=config.get_str('bhs_sendgrid_mailer.tracking_mode', 'off'),
            sendgrid_api_polling_active=config.get_bool('bhs_sendgrid_mailer.api_polling_active', False),
            sendgrid_api_polling_interval=config.get_int('bhs_sendgrid_mailer.api_polling_interval', 15),
            sendgrid_api_polling_window_days=config.get_int('bhs_sendgrid_mailer.api_polling_window_days', 7),
            sendgrid_dedupe_mode=config.get_str('bhs_sendgrid_mailer.dedupe_mode', 'batch_email'),
            queue_retention_days=config.get_int('bhs_sendgrid_mailer.queue_retention_days', 90),
            event_retention_days=config.get_int('bhs_sendgrid_mailer.event_retention_days', 30),
            sync_line_retention_days=config.get_int('bhs_sendgrid_mailer.sync_line_retention_days', 30),
            sync_run_retention_days=config.get_int('bhs_sendgrid_mailer.sync_run_retention_days', 365),
            sendgrid_api_key=False,
            google_service_account_json=False,
            sendgrid_webhook_public_key=False,
        )
        return res
