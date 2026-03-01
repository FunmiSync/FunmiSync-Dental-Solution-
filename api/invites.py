from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from auth.oauth2 import get_current_user
from core.database import get_db
from core.models import Users, MemberInvite, RoleAssignment, ScopeType, RoleType
from core.schemas import create_dso_invite_request, create_clinic_invite_request, accept_invite_request, invite_out
from infra.rbac import require_dso_manage, require_clinic_access, get_dso_role, get_clinic_role
from config import settings
import hashlib
import secrets
import logging

logger = logging.getLogger()
router = APIRouter(
    prefix= "/invites",
    tags=["Invites"]
)

INVITE_TTL_HOURS = settings.invite_ttl_hours

def _now() -> datetime:
    return datetime.now(timezone.utc)

def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@router.post("dso/{dso_id}",status_code= status.HTTP_201_CREATED, response_model= invite_out )
async def create_dso_invite(dso_id: str, payload: create_dso_invite_request, current_user: Users = Depends(get_current_user), db: Session = Depends(get_db)):
    
    actor = require_dso_manage(db = db, user_id = current_user.id, dso_id = dso_id)

    if actor.role == RoleType.MANAGER and payload.role == "manager":
        logger.warning("Manager can only add staff", extra= {
            "user" : current_user.id,
            "role" : actor.role
            } )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager can only invite staff")

    now = _now()
    email = payload.email.lower().strip()

    #invalidating old tokens 
    old = db.query(MemberInvite).filter(MemberInvite.email == email, MemberInvite.scope_type == ScopeType.DSO, MemberInvite.dso_id == dso_id, MemberInvite.accepted_at.is_(None), MemberInvite.revoked_at.is_(None)).all()

    for row in old:
        row.revoked_at = now 

    raw_token = secrets.token_urlsafe(48)
    invite = MemberInvite(
        email = email,
        token_hash = hash_token(raw_token),
        scope_type = ScopeType.DSO,
        dso_id = dso_id, 
        role = RoleType(payload.role),
        created_by = current_user.id,
        expires_at = now + timedelta(hours= INVITE_TTL_HOURS)
    )

    try:
        logger.info(
        "DSO invite created",
        extra={"actor_user_id": current_user.id, "email": email, "dso_id": dso_id, "role": payload.role},
        )
        db.add(invite)
        db.commit()
    except SQLAlchemyError:
        logger.exception(
        "Invite accept failed: database error",
        extra={"user_id": current_user.id},
        )
        db.rollback()
        raise HTTPException(status_code= status.HTTP_500_INTERNAL_SERVER_ERROR, detail= "Failed o create Dso invite")

    return{
        "message": "Dso invite created",
        

    }