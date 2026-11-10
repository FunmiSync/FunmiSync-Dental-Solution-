from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.schemas.api import Webhook_requests, webhook_response
from app.security.webhook_secret import WEBHOOK_SECRET_HEADER, verify_webhook_secret_header
from app.services.crm_webhook_service import accept_webhook_event, get_webhook_clinic, record_webhook_auth_failure

router = APIRouter(
    prefix="/webhook",
    tags=["CRM Webhooks"]
)


@router.post("/{crm_type}/{clinic_id}", status_code=202, response_model=webhook_response)
async def webhooks(
    crm_type: str,
    clinic_id: UUID,
    request: Request,
    payload: Webhook_requests,
    db: AsyncSession = Depends(get_db),
):
    clinic = await get_webhook_clinic(db, clinic_id=clinic_id, crm_type=crm_type)

    try:
        verify_webhook_secret_header(
            provided_secret=request.headers.get(WEBHOOK_SECRET_HEADER),
            stored_secret_encrypted=clinic.webhook_secret,
        )
    except HTTPException:
        await record_webhook_auth_failure(db, clinic, crm_type=crm_type)
        raise

    return await accept_webhook_event(db, clinic, crm_type=crm_type, payload=payload.model_dump())
