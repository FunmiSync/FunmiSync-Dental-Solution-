from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import webhook_config_out
from app.security.oauth2 import get_current_user
from app.security.rbac import require_clinic_manage
from app.services.crm_webhook_service import webhook_config_for

router= APIRouter(
    prefix= "/webhook",
    tags= ["WEBHOOK_CONFIG"]
    )


@router.get("/{clinic_id}/webhook-config", response_model= webhook_config_out)
async def get_clinic_webhook_config(clinic_id: UUID, current_user: Users = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    clinic = await require_clinic_manage(db=db, user_id=current_user.id, clinic_id=clinic_id)
    return webhook_config_for(clinic)
