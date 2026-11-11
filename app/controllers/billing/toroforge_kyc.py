from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.billing.dependencies import get_toroforge_kyc_service
from app.controllers.billing.errors import raise_kyc_http_error
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import toroforge_wallet_kyc_status_response
from app.security.oauth2 import get_current_user
from app.security.wallet_access import get_wallet_for_access_check, require_wallet_manage_access, require_wallet_view_access
from app.services.billing.kyc_service import ToroForgeKYCService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge KYC"],
)


def _wallet_log_context(request: Request, current_user: Users, wallet_id: UUID, wallet) -> dict:
    return {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "wallet_id": str(wallet_id),
        "clinic_id": str(wallet.clinic_id) if wallet.clinic_id else None,
        "dso_id": str(wallet.dso_id) if wallet.dso_id else None,
    }


@router.post(
    "/wallets/{wallet_id}/kyc",
    status_code=200,
)
async def start_wallet_kyc(
    wallet_id: UUID,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    kyc_service: ToroForgeKYCService = Depends(get_toroforge_kyc_service),
):
    wallet = await get_wallet_for_access_check(db=db, wallet_id=wallet_id)
    await require_wallet_manage_access(db=db, user_id=current_user.id, wallet=wallet)

    log_ctx = _wallet_log_context(request, current_user, wallet_id, wallet)
    logger.info("ToroForge wallet KYC redirect requested", extra=log_ctx)

    try:
        kyc_url = await kyc_service.get_wallet_kyc_link(wallet_id=wallet_id)
    except Exception as exc:
        logger.exception("ToroForge wallet KYC redirect failed", extra=log_ctx)
        raise_kyc_http_error(exc)

    logger.info("ToroForge wallet KYC redirect generated", extra=log_ctx)
    return kyc_url


@router.get(
    "/wallets/{wallet_id}/kyc-status",
    status_code=status.HTTP_200_OK,
    response_model=toroforge_wallet_kyc_status_response,
)
async def get_wallet_kyc_status(
    wallet_id: UUID,
    request: Request,
    refresh: bool = Query(default=False),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    kyc_service: ToroForgeKYCService = Depends(get_toroforge_kyc_service),
):
    wallet = await get_wallet_for_access_check(db=db, wallet_id=wallet_id)
    await require_wallet_view_access(db=db, user_id=current_user.id, wallet=wallet)

    log_ctx = _wallet_log_context(request, current_user, wallet_id, wallet)
    logger.info("ToroForge wallet KYC status requested", extra={**log_ctx, "refresh": refresh})

    try:
        provider = None
        if refresh:
            status_response, kyc_verified = await kyc_service.check_wallet_kyc_status(wallet_id=wallet_id)
            provider = status_response.get("provider")
        else:
            kyc_verified = await kyc_service.get_cached_wallet_kyc_status(wallet_id=wallet_id)
    except Exception as exc:
        logger.exception("ToroForge wallet KYC status failed", extra=log_ctx)
        raise_kyc_http_error(exc)

    logger.info(
        "ToroForge wallet KYC status completed",
        extra={**log_ctx, "refresh": refresh, "verified": kyc_verified},
    )
    return {
        "wallet_id": wallet_id,
        "verified": kyc_verified,
        "provider": provider,
    }
