import hashlib
import hmac
import json

from cryptography.fernet import Fernet
from app.config import settings

ENCRYPTION_KEY = settings.encryption_key
fernet = Fernet(ENCRYPTION_KEY.encode() if isinstance(ENCRYPTION_KEY, str) else ENCRYPTION_KEY)
HASH_KEY = (settings.hash_key or settings.encryption_key).encode("utf-8")

def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    token = fernet.encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decode_secret(value: str | None) -> str | None:
    if value is None:
        return None
    token_byte = value.encode("utf-8")
    value_byte = fernet.decrypt(token_byte)
    return value_byte.decode ("utf-8")


def encrypt_json_secret(value):
    if value is None:
        return None
    return encrypt_secret(json.dumps(value, separators=(",", ":"), sort_keys=True))


def decode_json_secret(value: str | None):
    if value is None:
        return None
    decoded = decode_secret(value)
    if decoded is None:
        return None
    return json.loads(decoded)


def hash_lookup(value: str) -> str:
    return hmac.new(HASH_KEY, value.encode("utf-8"), hashlib.sha256).hexdigest()


def fingerprint_value(value: str | None, *, length: int = 12) -> str | None:
    if not value:
        return None
    return hash_lookup(value)[:length]
