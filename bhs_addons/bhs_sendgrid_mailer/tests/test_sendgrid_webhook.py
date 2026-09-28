import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from odoo.tests import TransactionCase, tagged

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
            payload, signature, timestamp, public_key,
        ))
        self.assertFalse(SendGridWebhookVerifier.verify(
            payload + b' ', signature, timestamp, public_key,
        ))
