import json
import logging
from odoo import fields, http
from odoo.http import request

from ..services.sendgrid_webhook_verifier import SendGridWebhookVerifier

_logger = logging.getLogger(__name__)


class SendGridWebhook(http.Controller):

    @http.route('/webhook/sendgrid', type='http', auth='public', methods=['POST'], csrf=False)
    def sendgrid_webhook(self, **post):
        try:
            payload_bytes = request.httprequest.data
            if not payload_bytes:
                return request.make_response("No payload", status=400)

            config = request.env['ir.config_parameter'].sudo()
            public_key = config.get_str(
                'bhs_sendgrid_mailer.webhook_public_key', '',
            )
            signature = request.httprequest.headers.get(
                'X-Twilio-Email-Event-Webhook-Signature', '',
            )
            timestamp = request.httprequest.headers.get(
                'X-Twilio-Email-Event-Webhook-Timestamp', '',
            )
            if not SendGridWebhookVerifier.verify(
                payload_bytes, signature, timestamp, public_key,
            ):
                _logger.warning('Rejected SendGrid webhook with an invalid signature.')
                return request.make_response("Invalid signature", status=401)

            payload = payload_bytes.decode('utf-8')
            events = json.loads(payload)
            if not isinstance(events, list):
                events = [events]

            Queue = request.env['bhsoft.mail.queue'].sudo()
            event_names = sorted({
                event.get('event')
                for event in events
                if isinstance(event, dict)
                and isinstance(event.get('event'), str)
                and event.get('event')
            })
            config.set_str('bhs_sendgrid_mailer.last_webhook_received_at', fields.Datetime.to_string(fields.Datetime.now()))
            config.set_str('bhs_sendgrid_mailer.last_webhook_events', ', '.join(event_names) or 'test')
            config.set_int('bhs_sendgrid_mailer.last_webhook_event_count', len(events))
            for event in events:
                if not isinstance(event, dict):
                    continue
                event_type = event.get('event')
                if not isinstance(event_type, str) or not event_type:
                    continue

                queue_record = self._find_queue_record(Queue, event)
                if queue_record:
                    queue_record._apply_sendgrid_event(event_type, event, source='webhook')
                else:
                    Event = request.env['bhsoft.mail.event'].sudo()
                    event_time = Queue._sendgrid_event_datetime(event)
                    event_recorded = Event.record_unmatched_once(
                        event_type, event, event_time=event_time,
                    )
                    suppression = request.env['bhsoft.email.suppression'].sudo().browse()
                    if event_recorded:
                        suppression = request.env['bhsoft.email.suppression'].sudo().upsert_from_sendgrid_event(
                            event.get('email'), event_type, event,
                        )
                    if event_recorded and not suppression:
                        _logger.warning(
                            'No bhsoft.mail.queue record found for SendGrid event type %s and message ID %s.',
                            event_type,
                            event.get('sg_message_id') or event.get('smtp-id') or event.get('msg_id') or 'unknown',
                        )

            return request.make_response("OK", status=200)

        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            _logger.warning('Rejected malformed SendGrid webhook payload.')
            return request.make_response("Invalid payload", status=400)
        except Exception:
            _logger.exception('Error processing SendGrid webhook.')
            return request.make_response("Error", status=500)

    def _find_queue_record(self, Queue, event):
        queue_id = event.get('queue_id') or event.get('bhsoft_queue_id')
        if queue_id:
            try:
                record = Queue.browse(int(queue_id)).exists()
                if record:
                    return record
            except Exception:
                pass

        source_row_uuid = event.get('source_row_uuid')
        if source_row_uuid:
            record = Queue.search([('source_row_uuid', '=', source_row_uuid)], limit=1)
            if record:
                return record

        sg_message_id = event.get('sg_message_id') or event.get('smtp-id') or event.get('msg_id')
        if sg_message_id:
            base_msg_id = str(sg_message_id).split('.')[0]
            record = Queue.search([('sendgrid_msg_id', '=', base_msg_id)], limit=1)
            if record:
                return record
            candidates = Queue.search([
                ('sendgrid_msg_id', '=like', f'{base_msg_id}.%'),
            ], limit=2)
            if len(candidates) == 1:
                return candidates

        return Queue.browse()
