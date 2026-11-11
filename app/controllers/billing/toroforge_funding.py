from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.billing.dependencies import get_toroforge_funding_service
from app.controllers.billing.errors import raise_funding_http_error
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import toroforge_wallet_deposit_verify_request, toroforge_wallet_deposit_verify_response, toroforge_wallet_funding_initialize_request, toroforge_wallet_funding_initialize_response
from app.security.oauth2 import get_current_user
from app.security.wallet_access import get_wallet_for_access_check, require_wallet_funding_access
from app.services.billing.funding_service import ToroForgeFundingService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Funding"],
)


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
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    funding_service: ToroForgeFundingService = Depends(get_toroforge_funding_service),
):
    wallet = await get_wallet_for_access_check(db=db, wallet_id=wallet_id)
    await require_wallet_funding_access(db=db, user_id=current_user.id, wallet=wallet)

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
        raise_funding_http_error(exc)

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
    db: AsyncSession = Depends(get_db),
    funding_service: ToroForgeFundingService = Depends(get_toroforge_funding_service),
):
    wallet = await get_wallet_for_access_check(db=db, wallet_id=wallet_id)
    await require_wallet_funding_access(db=db, user_id=current_user.id, wallet=wallet)

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
        raise_funding_http_error(exc)

    logger.info(
        "ToroForge wallet deposit verification completed",
        extra={**log_ctx, "new_cached_balance_minor": result.get("new_cached_balance_minor")},
    )
    return result
