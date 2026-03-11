from core.queue import async_redis
from fastapi import HTTPException, status, Request
from  redis.exceptions import RedisError
import logging

logger = logging.getLogger(__name__)

MAX_LOGIN_ATTEMPTS = 5 
LOCK_WINDOWS_SECONDS = 30 * 60 

async def login_attempts(email : str , ip: str ):
    return f"login attempts : {email} : {ip}"

async def get_redis_attempts(key) -> int:
    try:
        attempts_raw = await async_redis.get(key)
        return int(attempts_raw) if attempts_raw is not None else 0
    except RedisError:
        logger.exception("Redis unavailable; login rate-limit read skipped", extra={"key": key})
        return 0

async def increment_attempts_with_key(key):
    try:
        attempts = await async_redis.incr(key)
        if attempts >= MAX_LOGIN_ATTEMPTS:
            await async_redis.expire(key, LOCK_WINDOWS_SECONDS)
        return int(attempts)
    except RedisError:
        logger.exception("Redis unavailable; login rate-limit read skipped", extra={"key": key})
        return 0


async def  handle_failed_login(key):
    new_attempt = await increment_attempts_with_key(key)
    if new_attempt >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException( status.HTTP_429_TOO_MANY_REQUESTS, detail= "Too many login attempt please Try again Later")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "Invalid Email or Password ")


def get_client_ip(request: Request):
    X_forwarded_for = request.headers.get("X_forwarded_for")
    if X_forwarded_for:
        return X_forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"
    






