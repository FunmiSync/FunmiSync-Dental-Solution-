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


def optional_cache(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (RedisError, InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Optional cache operation failed: %s", function.__name__, exc_info=True)
            return None
    return wrapped
