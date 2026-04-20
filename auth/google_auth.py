from fastapi import HTTPException, status
import logging
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from config import settings


logger = logging.getLogger(__name__)


CLIENT_ID = settings.google_client_id

def verify_google_credentials(credential: str) -> dict:
    try:
        idinfo= id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            CLIENT_ID
        )

    except ValueError as e :
        logger.warning("Google credential verification failed")
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail= "Invalid Google Credentails"
        ) from e 
    

    issuer = idinfo.get("iss")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        logger.warning("Google credentials rejected due to invalid issuer", extra= {"issuer": issuer})
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail= "Invalid Google Issuer"
        )
    
    sub = idinfo.get("sub")
    email = idinfo.get("email")
    email_verified = idinfo.get("email_verified")
    name = idinfo.get("name")

    if not sub or not email:
        logger.warning(
            "Google credential missing identity fields",
            extra={"has_sub": bool(sub), "has_email": bool(email)},
        )
        raise HTTPException(
            status_code= status.HTTP_401_UNAUTHORIZED,
            detail = "Incomplete Google identity"
        )
    
    if email_verified is not True:
        logger.warning(
            "Google credential rejected because email is not verified",
            extra={"email": str(email).strip().lower()},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google email is not verified",
        )
    
    return {
        "sub": sub,
        "email": str(email).strip().lower(),
        "name": str(name or "").strip(),
    }