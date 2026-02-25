import hmac
import secrets
from fastapi import HTTPException, Response, status, Request
from auth.oauth2 import REFRESH_TOKEN_EXPIRE_DAYS
import logging

logger = logging.getLogger(__name__)

CRSF_COOKIE_NAME = "csrf_token"
CRSF_HEADER_NAME = "X-CSRF-Token"

def make_csrf_token() -> str:
    return secrets.token_urlsafe(32)

def set_csrf_token(response: Response, crsf_token: str):
    response.set_cookie(
        key= CRSF_COOKIE_NAME,
        value= crsf_token,
        httponly= False,
        secure= True,
        samesite= "none",
        max_age= REFRESH_TOKEN_EXPIRE_DAYS * 24 *60 *60,
        path= "/"
    )

def verify_csrf(csrf_cookie: str | None, csrf_header: str | None, request: Request) : 
    if not csrf_cookie or not csrf_header:
        logger.warning(
        "csrf validation failed",
        extra={"reason": "mismatch", "path": request.url.path, "method": request.method},
        )
        raise HTTPException(status_code= status.HTTP_403_FORBIDDEN, detail = "CSRF validation Failed")
    if not hmac.compare_digest(csrf_cookie, csrf_header):
        logger.warning(
        "csrf validation failed",
        extra={"reason": "mismatch", "path": request.url.path, "method": request.method},
        )
        raise HTTPException(status_code= status.HTTP_403_FORBIDDEN, detail = "CSRF validation Failed")