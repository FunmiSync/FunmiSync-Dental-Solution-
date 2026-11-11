from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import accept_invite_request, create_clinic_invite_request, create_dso_invite_request, invite_out
from app.security.oauth2 import get_current_user
from app.security.rbac import require_clinic_manage, require_dso_manage
from app.services import invite_service

router = APIRouter(
    prefix= "/invites",
    tags=["Invites"]
)


@router.post("/dso/{dso_id}",status_code= status.HTTP_201_CREATED, response_model= invite_out )
async def create_dso_invite(dso_id: UUID, payload: create_dso_invite_request, current_user: Users = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    actor = await require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)
    return await invite_service.create_dso_invite(
        db,
        dso_id=dso_id,
        actor=actor,
        email=payload.email,
        role=payload.role,
        created_by=current_user.id,
    )


@router.post("/clinic/{clinic_id}", status_code = status.HTTP_201_CREATED)
async def create_clinic_invite(
    clinic_id: UUID,
    payload: create_clinic_invite_request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    clinic = await require_clinic_manage(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return await invite_service.create_clinic_invite(
        db,
        clinic=clinic,
        email=payload.email,
        role=payload.role,
        created_by=current_user.id,
    )


@router.post("/accept", status_code=status.HTTP_201_CREATED)
async def accept_invite(
    payload: accept_invite_request,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await invite_service.accept_invite(db, token=payload.token, user=current_user)
