import logging
from typing import NoReturn

from fastapi import Request
from redis.exceptions import RedisError

from app.infrastructure.queue import async_redis
from app.infrastructure.rate_counter import increment_window
from app.security.encryption import hash_lookup
from app.services.errors import AuthenticationFailed, TooManyRequests

logger = logging.getLogger(__name__)

MAX_LOGIN_ATTEMPTS = 5 
LOCK_WINDOWS_SECONDS = 30 * 60 

def login_attempts(email: str, ip: str) -> str:
    return f"login-attempts:{hash_lookup(email.strip().lower() + ':' + ip)}"

async def get_redis_attempts(key) -> int:
    try:
        attempts_raw = await async_redis.get(key)
        return int(attempts_raw) if attempts_raw is not None else 0
    except RedisError:
        logger.exception("Redis unavailable; login rate-limit read skipped", extra={"key": key})
        return 0

async def increment_attempts_with_key(key):
    try:
        return await increment_window(async_redis, key, LOCK_WINDOWS_SECONDS)
    except RedisError:
        logger.exception("Redis unavailable; login rate-limit read skipped", extra={"key": key})
        return 0


async def handle_failed_login(key) -> NoReturn:
    new_attempt = await increment_attempts_with_key(key)
    if new_attempt >= MAX_LOGIN_ATTEMPTS:
        raise TooManyRequests("Too many login attempt please Try again Later")
    raise AuthenticationFailed("Invalid Email or Password ")


async def clear_attempts_with_key(key):
    try:
        await async_redis.delete(key)
    except RedisError:
        logger.exception("Redis unavailable; login rate-limit clear skipped", extra={"key": key})


def get_client_ip(request: Request):
    if request.client is not None:
        return request.client.host
    return "unknown"



