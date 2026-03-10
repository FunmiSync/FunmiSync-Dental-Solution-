from fastapi import Depends, APIRouter
from auth.oauth2 import get_current_user
from core.database import get_db
from core.schemas import webhook_config_out
from core.models import Users
from sqlalchemy.orm import Session
from infra.rbac import require_clinic_manage
router= APIRouter( 
    prefix= "/webhook",
    tags= ["WEBHOOK_CONFIG"]
    )

@router.get("/{clinic_id}/webhook-config", response_model= webhook_config_out)
async def get_clinic_webhook_config(clinic_id: str, current_user: Users = Depends(get_db), db: Session = Depends(get_db)):
    clinic = require_clinic_manage(db=db, user_id = current_user.id, clinic_id= clinic_id)
