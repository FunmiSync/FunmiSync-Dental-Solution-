from fastapi import APIRouter, Depends, HTTPException
from core.models import Users, RoleType, RoleAssignment, ScopeType, RoleType
from core.schemas import add_dso_member_request
from auth.oauth2 import get_current_user
from core.database import get_db
from sqlalchemy.orm import Session
from infra.rbac import require_dso_manage, require_clinic_access
import logging

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix= "/dso",
    tags=["DSO Members"]
)

@router.post("/{dso_id}/members/accept")
async def add_dso_members(dso_id: str, payload:add_dso_member_request, current_user: Users = Depends(get_current_user), db: Session = Depends(get_db)):
    actor = require_dso_manage(db = db, user_id = current_user.id, dso_id= dso_id )
    if actor.role == RoleType.MANAGER and payload.role == "manager":
        logger.warning("Manager can only add staff", extra= {
            "user" : current_user.id,
            "role" : actor.role
            } )


    new_assignment = RoleAssignment(
        user_id = payload.user_id,
        scope_type = ScopeType.DSO,
        dso_id = dso_id,
        role = RoleType(payload.role)
        created        
    )

