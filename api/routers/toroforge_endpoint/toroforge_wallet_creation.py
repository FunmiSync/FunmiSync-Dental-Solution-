from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status, Header
from sqlalchemy.orm import Session

from auth.oauth2 import get_current_user
from billing.toroforge.exceptions import (
    ToroForgeDuplicateNameError,
    ToroForgeValidationError,
    ToroForgeWalletCreationError,
)
from billing.toroforge.toroforge_client.client import ToroForgeClient
from billing.toroforge.toroforge_client.keystore_client import ToroForgeKeyStoreClient
from billing.toroforge.toroforge_client.tns_client import ToroForgeTNSClient
from billing.toroforge.toroforge_config import get_toroforge_config
from billing.toroforge.toroforge_service.wallet_service import ToroForgeWalletService
from core.database import get_db
from core.models import RegisteredClinics, Users
from core.schemas import (
    toroforge_wallet_create_request,
    toroforge_wallet_create_response,
)
from infra.rbac import require_clinic_manage, require_dso_manage

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/toroforge",
    tags=["ToroForge Wallets"],
)


async def get_toroforge_wallet_service(
    db: Session = Depends(get_db),
) -> AsyncIterator[ToroForgeWalletService]:
    config = get_toroforge_config()
    base_client = ToroForgeClient(
        config=config,
        breaker=ToroForgeClient.toroforge_breaker,
    )

    try:
        yield ToroForgeWalletService(
            db=db,
            keystore_client=ToroForgeKeyStoreClient(base_client),
            tns_client=ToroForgeTNSClient(base_client),
        )
    finally:
        await base_client.aclose()
        


def _raise_wallet_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeDuplicateNameError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=message or "ToroForge username is already taken",
        ) from exc

    if isinstance(exc, ToroForgeValidationError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message or "Invalid ToroForge wallet request",
        ) from exc

    if isinstance(exc, ToroForgeWalletCreationError):
        if "already exists" in lowered or "already taken" in lowered or "partial external state" in lowered:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=message,
            ) from exc

        if "not found" in lowered:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=message,
            ) from exc

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to provision ToroForge wallet at this time",
        ) from exc

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected error while creating ToroForge wallet",
    ) from exc


def  require_standalone_clinic_wallet_create_access(
    *,
    db: Session,
    user_id: UUID,
    clinic_id: UUID,
) -> RegisteredClinics:
    clinic = require_clinic_manage(db=db, user_id=user_id, clinic_id=clinic_id)

    if clinic.dso_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clinic belongs to a DSO. Use the DSO clinic wallet endpoint.",
        )

    return clinic


def require_dso_clinic_wallet_create_access(
    *,
    db: Session,
    user_id: UUID,
    dso_id: UUID,
    clinic_id: UUID,
) -> RegisteredClinics:
    clinic = require_clinic_manage(db=db, user_id=user_id, clinic_id=clinic_id)

    if clinic.dso_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clinic is not under a DSO. Use the standalone clinic wallet endpoint.",
        )

    if clinic.dso_id != dso_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinic does not belong to this DSO.",
        )

    return clinic


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
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    clinic = require_standalone_clinic_wallet_create_access(
        db=db,
        user_id=current_user.id,
        clinic_id=clinic_id,
    )

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
            initiated_by_user_id= current_user.id

        )
    except Exception as exc:
        logger.exception("ToroForge standalone clinic wallet create failed", extra=log_ctx)
        _raise_wallet_http_error(exc)

    logger.info(
        "ToroForge standalone clinic wallet created successfully",
        extra={**log_ctx, "wallet_id": str(result.wallet_id)},
    )

    return {
        "wallet_id": result.wallet_id,
        "scope_type": "clinic",
        "clinic_id": clinic.id,
        "external_wallet_address": result.external_wallet_address,
        "external_wallet_username": result.external_wallet_username,
        "generated_password": result.generated_password,
    }


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
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    clinic = require_dso_clinic_wallet_create_access(
        db=db,
        user_id=current_user.id,
        dso_id=dso_id,
        clinic_id=clinic_id,
    )

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
            idempotency_key= idempotency_key,
            initiated_by_user_id=current_user.id
        )
    except Exception as exc:
        logger.exception("ToroForge DSO clinic wallet create failed", extra=log_ctx)
        _raise_wallet_http_error(exc)

    logger.info(
        "ToroForge DSO clinic wallet created successfully",
        extra={**log_ctx, "wallet_id": str(result.wallet_id)},
    )

    return {
        "wallet_id": result.wallet_id,
        "scope_type": "clinic",
        "clinic_id": clinic.id,
        "dso_id": dso_id,
        "external_wallet_address": result.external_wallet_address,
        "external_wallet_username": result.external_wallet_username,
        "generated_password": result.generated_password,
    }


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
    db: Session = Depends(get_db),
    idempotency_key: str = Header(..., alias= "Idempotency-key"),
    wallet_service: ToroForgeWalletService = Depends(get_toroforge_wallet_service),
):
    require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)

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
            idempotency_key= idempotency_key,
            initiated_by_user_id= current_user.id
        )
    except Exception as exc:
        logger.exception("ToroForge DSO treasury wallet create failed", extra=log_ctx)
        _raise_wallet_http_error(exc)

    logger.info(
        "ToroForge DSO treasury wallet created successfully",
        extra={**log_ctx, "wallet_id": str(result.wallet_id)},
    )

    return {
        "wallet_id": result.wallet_id,
        "scope_type": "dso",
        "dso_id": dso_id,
        "external_wallet_address": result.external_wallet_address,
        "external_wallet_username": result.external_wallet_username,
        "generated_password": result.generated_password,
    }
