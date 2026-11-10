from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, load_only

from app.security.oauth2 import get_current_user
from app.infrastructure.providers.toroforge.exceptions import ToroForgeAuthError, ToroForgeError, ToroForgeHTTPError, ToroForgeTimeoutError, ToroForgeUnavailableError, ToroForgeValidationError
from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.payment_client import ToroForgePaymentClient
from app.infrastructure.providers.toroforge.config import get_toroforge_config
from app.services.billing.funding_service import ToroForgeFundingService
from app.infrastructure.database import get_db
from app.models.entities import Users, Wallet
from app.schemas.api import toroforge_wallet_deposit_verify_request, toroforge_wallet_deposit_verify_response, toroforge_wallet_funding_initialize_request, toroforge_wallet_funding_initialize_response
from app.security.rbac import require_clinic_manage, require_dso_manage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Funding"],
)


async def get_toroforge_funding_service(
    db: Session = Depends(get_db),
) -> AsyncIterator[ToroForgeFundingService]:
    config = get_toroforge_config()
    base_client = ToroForgeClient(
        config=config,
        breaker=ToroForgeClient.toroforge_breaker,
    )

    try:
        yield ToroForgeFundingService(
            db=db,
            payment_client=ToroForgePaymentClient(base_client),
        )
    finally:
        await base_client.aclose()


def get_wallet_for_funding_access_check(
    *,
    db: Session,
    wallet_id: UUID,
) -> Wallet:
    wallet = (
        db.query(Wallet)
        .options(
            load_only(
                Wallet.id,
                Wallet.clinic_id,
                Wallet.dso_id,
            )
        )
        .filter(Wallet.id == wallet_id)
        .first()
    )

    if not wallet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Wallet not found",
        )

    if wallet.clinic_id is None and wallet.dso_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wallet is not linked to a clinic or DSO",
        )

    return wallet


def require_wallet_funding_access(
    *,
    db: Session,
    user_id: UUID,
    wallet: Wallet,
) -> None:
    if wallet.clinic_id is not None and wallet.dso_id is None:
        require_clinic_manage(db=db, user_id=user_id, clinic_id=wallet.clinic_id)
        return

    if wallet.dso_id is not None:
        require_dso_manage(db=db, user_id=user_id, dso_id=wallet.dso_id)
        return

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Wallet is not linked to a clinic or DSO",
    )


def _raise_funding_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeValidationError):
        if "not found" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message or "Funding resource not found",
            ) from exc

        if "already" in lowered or "posted successfully" in lowered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message or "Funding request conflicts with existing state",
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message or "Invalid ToroForge funding request",
        ) from exc

    if isinstance(exc, ToroForgeAuthError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="ToroForge funding provider authentication failed",
        ) from exc

    if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ToroForge funding provider is temporarily unavailable",
        ) from exc

    if isinstance(exc, (ToroForgeHTTPError, ToroForgeError)):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=message or "ToroForge funding provider error",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected error while processing ToroForge funding",
    ) from exc


@router.post(
    "/wallets/{wallet_id}/funding",
    status_code=status.HTTP_201_CREATED,
    response_model=toroforge_wallet_funding_initialize_response,
)
async def initialize_wallet_funding(
    wallet_id: UUID,
    payload: toroforge_wallet_funding_initialize_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    funding_service: ToroForgeFundingService = Depends(get_toroforge_funding_service),
):
    wallet = get_wallet_for_funding_access_check(db=db, wallet_id=wallet_id)
    require_wallet_funding_access(db=db, user_id=current_user.id, wallet=wallet)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "wallet_id": str(wallet_id),
        "idempotency_key": idempotency_key,
        "currency": payload.currency.strip().upper(),
        "payment_type": payload.payment_type,
    }

    logger.info("ToroForge wallet funding initialize requested", extra=log_ctx)

    try:
        result = await funding_service.initialize_wallet_funding(
            wallet_id=wallet_id,
            request_id=idempotency_key,
            amount=payload.amount,
            currency=payload.currency,
            payment_type=payload.payment_type,
            success_url=payload.success_url,
            cancel_url=payload.cancel_url,
            token=payload.token,
            payer_name=payload.payer_name,
            payer_address=payload.payer_address,
            payer_city=payload.payer_city,
            payer_state=payload.payer_state,
            payer_country=payload.payer_country,
            payer_zipcode=payload.payer_zipcode,
            payer_phone=payload.payer_phone,
            description=payload.description,
        )
    except Exception as exc:
        logger.exception("ToroForge wallet funding initialize failed", extra=log_ctx)
        _raise_funding_http_error(exc)

    logger.info(
        "ToroForge wallet funding initialize completed",
        extra={
            **log_ctx,
            "payment_transaction_id": result.get("payment_transaction_id"),
            "external_payment_id": result.get("external_payment_id"),
        },
    )

    return result


@router.post(
    "/wallets/{wallet_id}/funding/{payment_transaction_id}/verify-deposit",
    status_code=status.HTTP_200_OK,
    response_model=toroforge_wallet_deposit_verify_response,
)
async def verify_wallet_deposit(
    wallet_id: UUID,
    payment_transaction_id: UUID,
    payload: toroforge_wallet_deposit_verify_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
    funding_service: ToroForgeFundingService = Depends(get_toroforge_funding_service),
):
    wallet = get_wallet_for_funding_access_check(db=db, wallet_id=wallet_id)
    require_wallet_funding_access(db=db, user_id=current_user.id, wallet=wallet)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "wallet_id": str(wallet_id),
        "payment_transaction_id": str(payment_transaction_id),
        "txid": payload.txid.strip(),
    }

    logger.info("ToroForge wallet deposit verification requested", extra=log_ctx)

    try:
        result = await funding_service.verify_wallet_deposit(
            wallet_id=wallet_id,
            payment_transaction_id=payment_transaction_id,
            txid=payload.txid,
        )
    except Exception as exc:
        logger.exception("ToroForge wallet deposit verification failed", extra=log_ctx)
        _raise_funding_http_error(exc)

    logger.info(
        "ToroForge wallet deposit verification completed",
        extra={
            **log_ctx,
            "new_cached_balance_minor": result.get("new_cached_balance_minor"),
        },
    )

    return result
