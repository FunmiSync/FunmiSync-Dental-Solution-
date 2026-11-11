from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.billing.dependencies import get_toroforge_wallet_transfer_service
from app.controllers.billing.errors import raise_wallet_transfer_http_error
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import toroforge_wallet_transfer_request, toroforge_wallet_transfer_response
from app.security.oauth2 import get_current_user
from app.security.rbac import require_dso_manage
from app.services.billing.wallet_transfer_service import ToroForgeWalletTransferService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Wallet Transfers"],
)


@router.post(
    "/dsos/{dso_id}/clinics/{clinic_id}/wallet-transfer",
    status_code=status.HTTP_201_CREATED,
    response_model=toroforge_wallet_transfer_response,
)
async def transfer_from_dso_to_clinic(
    dso_id: UUID,
    clinic_id: UUID,
    payload: toroforge_wallet_transfer_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    transfer_service: ToroForgeWalletTransferService = Depends(
        get_toroforge_wallet_transfer_service
    ),
):
    await require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "dso_id": str(dso_id),
        "clinic_id": str(clinic_id),
        "currency": payload.currency.strip().upper(),
        "idempotency_key": idempotency_key,
    }
    logger.info("ToroForge DSO to clinic wallet transfer requested", extra=log_ctx)

    try:
        result = await transfer_service.transfer_from_dso_to_clinic(
            dso_id=dso_id,
            clinic_id=clinic_id,
            amount=payload.amount,
            currency=payload.currency,
            idempotency_key=idempotency_key,
            initiated_by_user_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("ToroForge wallet transfer failed", extra=log_ctx)
        raise_wallet_transfer_http_error(exc)

    logger.info(
        "ToroForge DSO to clinic wallet transfer completed",
        extra={
            **log_ctx,
            "wallet_transfer_id": result.get("wallet_transfer_id"),
            "external_transaction_id": result.get("external_transaction_id"),
            "status": result.get("status"),
        },
    )
    return result
