from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.controllers.billing.dependencies import get_toroforge_wallet_service
from app.controllers.billing.errors import raise_wallet_http_error
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import toroforge_wallet_create_request, toroforge_wallet_create_response
from app.security.csrf import verify_csrf
from app.security.oauth2 import get_current_user
from app.security.rbac import require_dso_manage
from app.security.wallet_access import (
    get_wallet_for_access_check,
    require_dso_clinic_wallet_create_access,
    require_standalone_clinic_wallet_create_access,
    require_wallet_manage_access,
)
from app.services.billing.wallet_service import ToroForgeWalletService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Wallets"],
)


def _created_wallet_response(result, *, scope_type: str, clinic_id: UUID | None = None, dso_id: UUID | None = None) -> dict:
    # None values are dropped by response_model_exclude_none on each route.
    return {
        "wallet_id": result.wallet_id,
        "scope_type": scope_type,
        "clinic_id": clinic_id,
        "dso_id": dso_id,
        "external_wallet_address": result.external_wallet_address,
        "external_wallet_username": result.external_wallet_username,
        "generated_password": result.generated_password,
    }


@router.post(
    "/clinics/{clinic_id}/wallet",
    status_code=status.HTTP_201_CREATED,
    response_model=toroforge_wallet_create_response,
    response_model_exclude_none=True,
)
async def create_standalone_clinic_wallet(
    clinic_id: UUID,
    payload: toroforge_wallet_create_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    clinic = await require_standalone_clinic_wallet_create_access(db=db, user_id=current_user.id, clinic_id=clinic_id)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "clinic_id": str(clinic.id),
        "username": payload.username.strip().lower(),
        "scope_type": "clinic",
        "is_dso_clinic": False,
    }
    logger.info("ToroForge standalone clinic wallet create requested", extra=log_ctx)

    try:
        result = await wallet_service.create_and_provision_clinic_wallet(
            clinic_id=clinic.id,
            username=payload.username,
            idempotency_key=idempotency_key,
            initiated_by_user_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("ToroForge standalone clinic wallet create failed", extra=log_ctx)
        raise_wallet_http_error(exc)

    logger.info("ToroForge standalone clinic wallet created successfully", extra={**log_ctx, "wallet_id": str(result.wallet_id)})
    return _created_wallet_response(result, scope_type="clinic", clinic_id=clinic.id)


@router.post(
    "/dsos/{dso_id}/clinics/{clinic_id}/wallet",
    status_code=status.HTTP_201_CREATED,
    response_model=toroforge_wallet_create_response,
    response_model_exclude_none=True,
)
async def create_dso_clinic_wallet(
    dso_id: UUID,
    clinic_id: UUID,
    payload: toroforge_wallet_create_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    clinic = await require_dso_clinic_wallet_create_access(db=db, user_id=current_user.id, dso_id=dso_id, clinic_id=clinic_id)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "dso_id": str(dso_id),
        "clinic_id": str(clinic.id),
        "username": payload.username.strip().lower(),
        "scope_type": "clinic",
        "is_dso_clinic": True,
    }
    logger.info("ToroForge DSO clinic wallet create requested", extra=log_ctx)

    try:
        result = await wallet_service.create_and_provision_clinic_wallet(
            clinic_id=clinic.id,
            username=payload.username,
            idempotency_key=idempotency_key,
            initiated_by_user_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("ToroForge DSO clinic wallet create failed", extra=log_ctx)
        raise_wallet_http_error(exc)

    logger.info("ToroForge DSO clinic wallet created successfully", extra={**log_ctx, "wallet_id": str(result.wallet_id)})
    return _created_wallet_response(result, scope_type="clinic", clinic_id=clinic.id, dso_id=dso_id)


@router.post(
    "/dsos/{dso_id}/wallet",
    status_code=status.HTTP_201_CREATED,
    response_model=toroforge_wallet_create_response,
    response_model_exclude_none=True,
)
async def create_dso_treasury_wallet(
    dso_id: UUID,
    payload: toroforge_wallet_create_request,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    await require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "dso_id": str(dso_id),
        "username": payload.username.strip().lower(),
        "scope_type": "dso",
    }
    logger.info("ToroForge DSO treasury wallet create requested", extra=log_ctx)

    try:
        result = await wallet_service.create_and_provision_dso_wallet(
            dso_id=dso_id,
            username=payload.username,
            idempotency_key=idempotency_key,
            initiated_by_user_id=current_user.id,
        )
    except Exception as exc:
        logger.exception("ToroForge DSO treasury wallet create failed", extra=log_ctx)
        raise_wallet_http_error(exc)

    logger.info("ToroForge DSO treasury wallet created successfully", extra={**log_ctx, "wallet_id": str(result.wallet_id)})
    return _created_wallet_response(result, scope_type="dso", dso_id=dso_id)


@router.post("/wallets/{wallet_id}/password/rotate", status_code=status.HTTP_200_OK)
async def update_password_for_wallet(
    wallet_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
    csrf_cookie: str | None = Cookie(default=None, alias="csrf_token"),
    csrf_header: str | None = Header(default=None, alias="X-CSRF-Token"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service)
):
    verify_csrf(csrf_cookie, csrf_header, request)
    wallet = await get_wallet_for_access_check(db=db, wallet_id=wallet_id)
    await require_wallet_manage_access(db=db, user_id=current_user.id, wallet=wallet)

    log_ctx = {
        "request_id": getattr(request.state, "request_id", None),
        "user_id": str(current_user.id),
        "wallet_id": str(wallet_id),
        "clinic_id": str(wallet.clinic_id) if wallet.clinic_id else None,
        "dso_id": str(wallet.dso_id) if wallet.dso_id else None,
    }
    logger.info("ToroForge wallet password rotation requested", extra=log_ctx)

    try:
        result = await wallet_service.update_wallet_password(wallet_id=wallet_id)
    except Exception as exc:
        logger.exception("ToroForge wallet password rotation failed", extra=log_ctx)
        raise_wallet_http_error(exc)

    return {
        "wallet_id": wallet_id,
        "rotated": True,
        "provider_result": result.get("result"),
    }
