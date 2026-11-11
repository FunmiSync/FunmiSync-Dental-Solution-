"""Start and end a browser session: rotate refresh state and manage auth cookies."""
import uuid

from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Users
from app.schemas.api import loginresponse
from app.security.csrf import make_csrf_token, set_csrf_token
from app.security.oauth2 import clear_stream_access_cookie, create_access_token, create_refresh_token, set_refresh_cookie, set_stream_access_cookie


async def start_login_session(*, user: Users, response: Response, db: AsyncSession) -> loginresponse:
    # A new refresh JTI invalidates any previously issued refresh token.
    user.refresh_jti = str(uuid.uuid4())
    await db.commit()

    access_token = create_access_token(user=user)
    refresh_token = create_refresh_token(user=user)

    set_stream_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)

    csrf_token = make_csrf_token()
    set_csrf_token(response, csrf_token)

    return loginresponse(access_token=access_token, csrf_token=csrf_token)


async def end_login_session(*, user: Users, response: Response, db: AsyncSession) -> None:
    # Bumping token_version revokes every access token issued before logout.
    user.token_version = int(user.token_version + 1)
    user.refresh_jti = None
    await db.commit()

    response.delete_cookie(key="refresh_token", path="/login/refresh")
    response.delete_cookie(key="csrf_token", path="/")
    clear_stream_access_cookie(response)
