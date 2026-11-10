"""Decide whether a sign-in attempt identifies an active user."""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.entities import Users
from app.security.oauth2 import verify_password
from app.services.auth.login_attempts import (
    MAX_LOGIN_ATTEMPTS,
    clear_attempts_with_key,
    get_redis_attempts,
    handle_failed_login,
    login_attempts,
)
from app.services.errors import AuthenticationFailed, NotFound, TooManyRequests

logger = logging.getLogger(__name__)


def _require_active(user: Users, *, log_message: str) -> None:
    if not user.is_active:
        logger.warning(log_message, extra={"user_id": str(user.id), "email": user.email})
        raise AuthenticationFailed("Account is deactivated")


async def authenticate_with_password(db: AsyncSession, *, email: str, password: str, client_ip: str) -> Users:
    email = email.strip().lower()
    key = login_attempts(email, client_ip)

    if await get_redis_attempts(key) >= MAX_LOGIN_ATTEMPTS:
        raise TooManyRequests("Too many login attempt please Try again Later")

    user = await db.scalar(select(Users).where(Users.email == email).limit(1))
    # bcrypt takes ~0.25 s of CPU; on the event loop it would stall every other request.
    if user and await run_in_threadpool(verify_password, password, hashed_password=user.password):
        _require_active(user, log_message="Login rejected: account is deactivated")
        await clear_attempts_with_key(key)
        return user

    # Counts the failure, then raises 401, or 429 once the limit is reached.
    await handle_failed_login(key)


async def authenticate_with_google(db: AsyncSession, *, google_user: dict) -> Users:
    user = await db.scalar(select(Users).where(Users.google_sub == google_user["sub"]).limit(1))

    if user is None:
        logger.warning("Google login rejected: no Google account found", extra={"email": google_user["email"]})
        raise NotFound("No Google account found. Sign up first.")

    _require_active(user, log_message="Google login rejected: account is deactivated")
    logger.info("Google login successful", extra={"user_id": str(user.id), "email": user.email})
    return user
