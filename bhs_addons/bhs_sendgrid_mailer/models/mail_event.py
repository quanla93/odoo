import hashlib
import json

from odoo import api, fields, models


class BhsoftMailEvent(models.Model):
    _name = 'bhsoft.mail.event'
    _description = 'SendGrid Mail Event'
    _order = 'event_time desc, id desc'

    queue_id = fields.Many2one(
        'bhsoft.mail.queue', required=True, readonly=True, ondelete='cascade', index=True,
    )
    event_key = fields.Char(required=True, readonly=True, index=True)
    event_type = fields.Char(required=True, readonly=True, index=True)
    event_time = fields.Datetime(readonly=True, index=True)
    source = fields.Selection([
        ('webhook', 'Webhook'),
        ('api', 'API Polling'),
    ], required=True, readonly=True, index=True)
    reason = fields.Text(readonly=True)

    _event_key_unique = models.UniqueIndex(
        '(event_key)',
        'This SendGrid event has already been processed.',
    )

    @api.model
    def record_once(self, queue, event_type, event_data, source, event_time=False):
        provider_event_id = event_data.get('sg_event_id') or event_data.get('event_id')
        if provider_event_id:
            identity = str(provider_event_id)
        else:
            identity_data = {
                'queue_id': queue.id,
                'message_id': event_data.get('sg_message_id')
                or event_data.get('smtp-id')
                or event_data.get('msg_id')
                or queue.sendgrid_msg_id,
                'event': event_type,
                'timestamp': event_data.get('timestamp') or event_data.get('processed'),
                'reason': event_data.get('reason') or event_data.get('response'),
                'url': event_data.get('url'),
            }
            serialized = json.dumps(
                identity_data, sort_keys=True, separators=(',', ':'), default=str,
            )
            identity = hashlib.sha256(serialized.encode('utf-8')).hexdigest()
        event_key = f'{queue.id}:{identity}'

        self.env.cr.execute("""
            INSERT INTO bhsoft_mail_event (
                queue_id, event_key, event_type, event_time, source, reason,
                create_uid, write_uid, create_date, write_date
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
        """, (
            queue.id,
            event_key,
            event_type,
            event_time or None,
            source,
            event_data.get('reason') or event_data.get('response') or None,
            self.env.uid,
            self.env.uid,
        ))
        return bool(self.env.cr.fetchone())
