from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from auth.oauth2 import get_current_user
from billing.toroforge.exceptions import (
    ToroForgeAuthError,
    ToroForgeError,
    ToroForgeHTTPError,
    ToroForgeTimeoutError,
    ToroForgeUnavailableError,
    ToroForgeValidationError,
)
from billing.toroforge.toroforge_client.client import ToroForgeClient
from billing.toroforge.toroforge_client.wallet_transfer_client import (
    ToroForgeWalletTransferClient,
)
from billing.toroforge.toroforge_config import get_toroforge_config
from billing.toroforge.toroforge_service.wallet_transfer_service import (
    ToroForgeWalletTransferService,
)
from core.database import get_db
from core.models import Users
from core.schemas import (
    toroforge_wallet_transfer_request,
    toroforge_wallet_transfer_response,
)
from infra.rbac import require_dso_manage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Wallet Transfers"],
)


async def get_toroforge_wallet_transfer_service(
    db: Session = Depends(get_db),
) -> AsyncIterator[ToroForgeWalletTransferService]:
    config = get_toroforge_config()

    base_client = ToroForgeClient(
        config=config,
        breaker=ToroForgeClient.toroforge_breaker,
    )

    try:
        yield ToroForgeWalletTransferService(
            db=db,
            transfer_client=ToroForgeWalletTransferClient(base_client),
        )
    finally:
        await base_client.aclose()



def _raise_wallet_transfer_http_error(exc: Exception) -> NoReturn:

    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeValidationError):
        if "not found" in lowered or "does not belong" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message or "Wallet transfer resource not found",
            ) from exc

        if "insufficient" in lowered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message or "Insufficient wallet balance",
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message or "Invalid wallet transfer request",
        ) from exc

    if isinstance(exc, ToroForgeAuthError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="ToroForge provider authentication failed",
        ) from exc

    if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ToroForge provider is temporarily unavailable",
        ) from exc

    if isinstance(exc, (ToroForgeHTTPError, ToroForgeError)):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=message or "ToroForge provider error",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected error while processing wallet transfer",
    ) from exc


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
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    transfer_service: ToroForgeWalletTransferService = Depends(
        get_toroforge_wallet_transfer_service
    ),
):
    require_dso_manage(
        db=db,
        user_id=current_user.id,
        dso_id=dso_id,
    )

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
        _raise_wallet_transfer_http_error(exc)


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