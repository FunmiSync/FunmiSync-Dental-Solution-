from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from auth.oauth2 import get_current_user
from core.database import get_db
from core.models import Users
from core.schemas import dso_clinic_disabled_out, dso_clinic_list_out
from infra.dso_clinic_page_service import build_dso_clinic_list, disable_dso_clinic
from infra.rbac import require_dso_access, require_dso_manage


router = APIRouter(prefix="/dsos", tags=["DSO Clinics"])


@router.get("/{dso_id}/clinics", response_model=dso_clinic_list_out)
async def get_dso_clinics(
    dso_id: UUID,
    search: str | None = Query(default=None, max_length=100),
    status_filter: Literal["active", "disabled"] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)

    return build_dso_clinic_list(
        db,
        dso_id=dso_id,
        user_id=current_user.id,
        search=search,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/{dso_id}/clinics/{clinic_id}/disable",
    response_model=dso_clinic_disabled_out,
    status_code=status.HTTP_200_OK,
)
async def disable_clinic_from_dso(
    dso_id: UUID,
    clinic_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)

    return disable_dso_clinic(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        disabled_by=current_user.id,
    )
