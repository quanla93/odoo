import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from odoo.tests import HttpCase, TransactionCase, tagged

from ..services.sendgrid_webhook_verifier import SendGridWebhookVerifier


@tagged('post_install', '-at_install')
class TestSendGridWebhookVerifier(TransactionCase):
    def test_verifies_raw_payload_signature(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode('utf-8')
        payload = b'[{"event":"delivered"}]'
        timestamp = '1758621000'
        signature = base64.b64encode(private_key.sign(
            timestamp.encode('utf-8') + payload,
            ec.ECDSA(hashes.SHA256()),
        )).decode('ascii')

        self.assertTrue(SendGridWebhookVerifier.verify(
            payload, signature, timestamp, public_key, now=int(timestamp),
        ))
        self.assertFalse(SendGridWebhookVerifier.verify(
            payload + b' ', signature, timestamp, public_key, now=int(timestamp),
        ))
        self.assertFalse(SendGridWebhookVerifier.verify(
            payload, signature, timestamp, public_key,
            now=int(timestamp) + 601,
        ))


@tagged('post_install', '-at_install')
class TestSendGridWebhookController(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = cls.private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode('utf-8')
        cls.public_key = public_key

    def _headers(self, payload, timestamp=None, valid=True):
        timestamp = timestamp or str(int(time.time()))
        signature = base64.b64encode(self.private_key.sign(
            timestamp.encode('utf-8') + payload,
            ec.ECDSA(hashes.SHA256()),
        )).decode('ascii')
        if not valid:
            signature = base64.b64encode(b'invalid').decode('ascii')
        return {
            'Content-Type': 'application/json',
            'X-Twilio-Email-Event-Webhook-Signature': signature,
            'X-Twilio-Email-Event-Webhook-Timestamp': timestamp,
        }

    def _post(self, value, valid=True):
        payload = value if isinstance(value, bytes) else json.dumps(value).encode()
        with self.registry.cursor() as cr:
            self.env(cr)['ir.config_parameter'].sudo().set_str(
                'bhs_sendgrid_mailer.webhook_public_key', self.public_key,
            )
            cr.commit()
        return self.url_open(
            '/webhook/sendgrid',
            data=payload,
            method='POST',
            headers=self._headers(payload, valid=valid),
        )

    def test_unmatched_definitive_events_create_suppressions(self):
        events = {
            'bounce': 'hard_bounce',
            'spamreport': 'spam_report',
            'unsubscribe': 'unsubscribe',
            'group_unsubscribe': 'unsubscribe',
        }
        for index, (event_type, expected_reason) in enumerate(events.items()):
            email = f'{event_type}@example.com'
            event_id = f'unmatched-{event_type}-{index}'
            response = self._post([{
                'event': event_type,
                'email': f' {email.upper()} ',
                'sg_event_id': event_id,
                'timestamp': 1_758_621_000 + index,
                'reason': 'provider reason',
            }])

            suppression = self.env['bhsoft.email.suppression'].search([
                ('normalized_email', '=', email),
            ])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(suppression.reason, expected_reason)
            self.assertEqual(suppression.provider, 'sendgrid')
            self.assertEqual(suppression.last_provider_event_id, event_id)

    def test_replayed_unmatched_event_does_not_reactivate_suppression(self):
        event = {
            'event': 'bounce',
            'email': 'replayed@example.com',
            'sg_event_id': 'replayed-event-1',
            'timestamp': 1_758_621_000,
        }
        self.assertEqual(self._post([event]).status_code, 200)
        suppression = self.env['bhsoft.email.suppression'].search([
            ('normalized_email', '=', 'replayed@example.com'),
        ])
        suppression.active = False

        self.assertEqual(self._post([event]).status_code, 200)

        suppression.invalidate_recordset(['active'])
        self.assertFalse(suppression.active)
        self.assertEqual(self.env['bhsoft.mail.event'].search_count([
            ('event_key', '=', 'unmatched:replayed-event-1'),
        ]), 1)

    def test_unmatched_non_definitive_events_do_not_suppress(self):
        for event_type in ('deferred', 'dropped', 'delivered'):
            email = f'{event_type}@example.com'
            response = self._post([{
                'event': event_type,
                'email': email,
                'sg_event_id': f'unmatched-{event_type}-1',
                'timestamp': 1_758_621_000,
            }])

            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.env['bhsoft.email.suppression'].search([
                ('normalized_email', '=', email),
            ]))

    def test_non_dictionary_entries_are_ignored(self):
        response = self._post([
            'not-an-event',
            None,
            {'event': 'spamreport', 'email': 'spam@example.com'},
        ])

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.env['bhsoft.email.suppression'].search([
            ('normalized_email', '=', 'spam@example.com'),
        ]))
        config = self.env['ir.config_parameter'].sudo()
        self.assertEqual(
            config.get_int('bhs_sendgrid_mailer.last_webhook_event_count'), 3,
        )
        self.assertEqual(
            config.get_str('bhs_sendgrid_mailer.last_webhook_events'), 'spamreport',
        )

    def test_non_string_event_type_and_message_id_are_ignored_safely(self):
        response = self._post([{
            'event': ['bounce'],
            'email': 'invalid-event@example.com',
            'sg_message_id': 12345,
        }, {
            'event': 'deferred',
            'email': 'temporary-id@example.com',
            'sg_message_id': 12345,
        }])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.env['bhsoft.email.suppression'].search([
            ('normalized_email', 'in', (
                'invalid-event@example.com', 'temporary-id@example.com',
            )),
        ]))

    def test_message_id_prefix_fallback_requires_one_candidate(self):
        Queue = self.env['bhsoft.mail.queue']
        first = Queue.create({
            'name': 'First',
            'email_to': 'first@example.com',
            'subject': 'Subject',
            'body_html': 'Body',
            'sendgrid_msg_id': 'message-1.first',
        })
        second = Queue.create({
            'name': 'Second',
            'email_to': 'second@example.com',
            'subject': 'Subject',
            'body_html': 'Body',
            'sendgrid_msg_id': 'message-1.second',
        })

        from ..controllers.sendgrid_webhook import SendGridWebhook
        controller = SendGridWebhook()
        self.assertFalse(controller._find_queue_record(Queue, {
            'sg_message_id': 'message-1.webhook',
        }))
        second.sendgrid_msg_id = 'different.second'
        self.assertEqual(controller._find_queue_record(Queue, {
            'sg_message_id': 'message-1.webhook',
        }), first)

    def test_stale_signed_payload_is_rejected(self):
        payload = json.dumps([{
            'event': 'bounce',
            'email': 'stale@example.com',
        }]).encode()
        with self.registry.cursor() as cr:
            self.env(cr)['ir.config_parameter'].sudo().set_str(
                'bhs_sendgrid_mailer.webhook_public_key', self.public_key,
            )
            cr.commit()
        response = self.url_open(
            '/webhook/sendgrid',
            data=payload,
            method='POST',
            headers=self._headers(
                payload, timestamp=str(int(time.time()) - 601),
            ),
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.env['bhsoft.email.suppression'].search([
            ('normalized_email', '=', 'stale@example.com'),
        ]))

    def test_invalid_signature_is_rejected(self):
        response = self._post([{
            'event': 'bounce',
            'email': 'invalid-signature@example.com',
        }], valid=False)

        self.assertEqual(response.status_code, 401)
        self.assertFalse(self.env['bhsoft.email.suppression'].search([
            ('normalized_email', '=', 'invalid-signature@example.com'),
        ]))

    def test_malformed_signed_payload_is_rejected(self):
        response = self._post(b'{not-json')

        self.assertEqual(response.status_code, 400)
