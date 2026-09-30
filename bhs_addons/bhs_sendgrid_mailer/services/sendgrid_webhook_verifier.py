import base64
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key


MAX_TIMESTAMP_AGE_SECONDS = 10 * 60


class SendGridWebhookVerifier:
    @staticmethod
    def _public_key(value):
        key = (value or '').strip()
        if not key:
            raise ValueError('The SendGrid webhook verification key is not configured.')
        if 'BEGIN PUBLIC KEY' not in key:
            key = f'-----BEGIN PUBLIC KEY-----\n{key}\n-----END PUBLIC KEY-----'
        return load_pem_public_key(key.encode('utf-8'))

    @classmethod
    def verify(cls, payload, signature, timestamp, public_key, now=None):
        if not signature or not timestamp:
            return False
        try:
            timestamp_value = int(timestamp)
            current_time = int(now if now is not None else time.time())
            if abs(current_time - timestamp_value) > MAX_TIMESTAMP_AGE_SECONDS:
                return False
            key = cls._public_key(public_key)
            decoded_signature = base64.b64decode(signature, validate=True)
            signed_payload = timestamp.encode('utf-8') + payload
            key.verify(decoded_signature, signed_payload, ec.ECDSA(hashes.SHA256()))
        except (ValueError, TypeError, InvalidSignature):
            return False
        return True
