from uuid import UUID
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.security.oauth2 import get_current_user
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import toroforge_clinic_billing_out, toroforge_dso_billing_out
from app.security.rbac import require_clinic_access, require_dso_access
from app.services.billing_service import build_dso_billing_command_center_cached, build_clinic_billing_command_center_cached



router = APIRouter(tags= ["Toroforge Billing"])


@router.get("/dsos/{dso_id}/billing/wallet_center", response_model= toroforge_dso_billing_out)
async def get_dso_wallet_command_center(
    dso_id: UUID,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db:Session = Depends(get_db)
):
    require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)
    return build_dso_billing_command_center_cached(db=db, dso_id=dso_id)


@router.get("/clinics/{clinic_id}/billing/wallet_center", response_model=toroforge_clinic_billing_out)
async def get_clinic_wallet_command_center(
    clinic_id: UUID,
    request: Request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return build_clinic_billing_command_center_cached(db=db, clinic_id=clinic_id) 