from fastapi import Depends, APIRouter
from auth.oauth2 import get_current_user
from core.database import get_db
from core.schemas import webhook_config_out
from core.models import Users
from sqlalchemy.orm import Session
from infra.rbac import require_clinic_manage
from config import settings
from auth.security import decode_secret
from uuid import UUID
router= APIRouter( 
    prefix= "/webhook",
    tags= ["WEBHOOK_CONFIG"]
    )

@router.get("/{clinic_id}/webhook-config", response_model= webhook_config_out)
async def get_clinic_webhook_config(clinic_id: UUID, current_user: Users = Depends(get_db), db: Session = Depends(get_db)):
    clinic = require_clinic_manage(db=db, user_id = current_user.id, clinic_id= clinic_id)
    header_value = decode_secret(clinic.webhook_secret)
    if header_value is None:
        raise ValueError("Webhook secret could not be decoded")
    return {
        "webhook_url": f"{settings.backend_base_url}/webhook/{clinic.crm_type}/{clinic.id}",
        "header_name": "X-Webhook-Secret",
        "header_value": header_value,
    }
