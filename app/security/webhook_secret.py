import hmac
from fastapi import HTTPException, status
from app.security.encryption import decode_secret
import logging

logger = logging.getLogger(__name__)


WEBHOOK_SECRET_HEADER =  "X-Webhook-Secret"

def verify_webhook_secret_header (*, provided_secret: str| None, stored_secret_encrypted: str | None):
    if not stored_secret_encrypted:
        raise HTTPException(status_code= status.HTTP_500_INTERNAL_SERVER_ERROR, detail= " Weebhook Secret is not configured for this clinic")
    
    if not provided_secret:
        raise HTTPException(status_code= status.HTTP_403_FORBIDDEN, detail= "Missing Weebhook secret")
    
    expected_secret = decode_secret(stored_secret_encrypted)
    if expected_secret is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret could not be decoded")

    if not hmac.compare_digest(provided_secret.strip(), expected_secret):
        raise HTTPException(status_code= status.HTTP_403_FORBIDDEN, detail= "Invalid webhook secret")
    
    
