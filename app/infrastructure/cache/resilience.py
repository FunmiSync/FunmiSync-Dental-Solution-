"""Optional caches must not turn a committed business operation into a failure."""
from functools import wraps
import json
import logging

from redis.exceptions import RedisError
from cryptography.fernet import InvalidToken
from app.security.encryption import encrypt_json_secret, decode_json_secret

logger = logging.getLogger(__name__)


def encode_cache_payload(value):
    return encrypt_json_secret(value)


def decode_cache_payload(value):
    # Legacy plaintext caches are misses; do not expose them via fallback reads.
    return decode_json_secret(value)


def decode_cached_text(raw) -> dict | list | None:
    """Decode a cached Redis value; anything unexpected is a cache miss."""
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        return None
    return decode_cache_payload(raw)


def optional_cache(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        try:
            return await function(*args, **kwargs)
        except (RedisError, OSError, InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Optional cache operation failed: %s", function.__name__, exc_info=True)
            return None
    return wrapped
