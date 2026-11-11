from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.oauth2 import get_current_user
from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import team_member_list_out
from app.security.rbac import require_clinic_access, require_dso_access
from app.services.team_member_service import build_clinic_team_members_cached, build_dso_team_members_cached

router = APIRouter(tags=["Team"])


@router.get("/dsos/{dso_id}/team", response_model=team_member_list_out)
async def get_dso_team_members(
    dso_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_dso_access(db=db, user_id=current_user.id, dso_id=dso_id)
    return await build_dso_team_members_cached(db, dso_id=dso_id)




@router.get("/clinics/{clinic_id}/team", response_model=team_member_list_out)
async def get_clinic_team_members(
    clinic_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await require_clinic_access(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return await build_clinic_team_members_cached(db, clinic_id=clinic_id)
