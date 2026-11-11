import logging

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.schemas.api import google_login_request, loginrequest, loginresponse, refreshresponse
from app.security.csrf import verify_csrf
from app.security.google import verify_google_credentials
from app.security.oauth2 import validate_refresh_token
from app.services.auth.authentication import authenticate_with_google, authenticate_with_password
from app.services.auth.login_attempts import get_client_ip
from app.services.auth.session import start_login_session

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix = "/login",
    tags = ["Auth"]
)


@router.post("", status_code=  200,  response_model= loginresponse)
async def login(payload: loginrequest, request: Request, response: Response, db:AsyncSession= Depends(get_db)):
    user = await authenticate_with_password(
        db,
        email=payload.email,
        password=payload.password,
        client_ip=get_client_ip(request),
    )
    return await start_login_session(user=user, response=response, db=db)


@router.post("/google", status_code=status.HTTP_200_OK, response_model= loginresponse)
async def login_with_google(
    payload: google_login_request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    google_user = await verify_google_credentials(payload.credential)
    user = await authenticate_with_google(db, google_user=google_user)
    return await start_login_session(user=user, response=response, db=db)


@router.post("/refresh", status_code= 200, response_model=refreshresponse)
async def refresh_access_token(response: Response, request: Request, db:AsyncSession = Depends(get_db), refresh_token: str | None= Cookie(default=None), csrf_cookie: str| None = Cookie(default= None, alias= "csrf_token"), csrf_header : str | None = Header(default= None, alias= "X-CSRF-Token")):
    verify_csrf(csrf_cookie, csrf_header, request)

    if not refresh_token:
        logger.warning(
            "Refresh denied: missing refresh token",
            extra={"request_id": request.state.request_id, "path": request.url.path},
        )
        raise HTTPException(status_code= status.HTTP_401_UNAUTHORIZED, detail = "Missing refreshtoken")

    user = await validate_refresh_token(refresh_token, db)
    # Refresh issues the same tokens and cookies as a fresh sign-in.
    return await start_login_session(user=user, response=response, db=db)
