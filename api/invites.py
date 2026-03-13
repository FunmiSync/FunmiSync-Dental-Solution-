from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from auth.oauth2 import get_current_user
from core.database import get_db
from core.models import Users, MemberInvite, RoleAssignment, ScopeType, RoleType
from core.schemas import create_dso_invite_request, create_clinic_invite_request, accept_invite_request, invite_out
from infra.rbac import require_dso_manage, require_clinic_manage, get_dso_role, get_clinic_role
from config import settings
import hashlib
import secrets
import logging
from uuid import UUID

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


@router.post("/dso/{dso_id}",status_code= status.HTTP_201_CREATED, response_model= invite_out )
async def create_dso_invite(dso_id: UUID, payload: create_dso_invite_request, current_user: Users = Depends(get_current_user), db: Session = Depends(get_db)):
    
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
        "invite_token": raw_token,
        "expires_at": invite.expires_at.isoformat()
        }




@router.post("/clinic/{clinic_id}", status_code = status.HTTP_201_CREATED)
async def create_clinic_invite(
    clinic_id: UUID,
    payload: create_clinic_invite_request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    clinic = require_clinic_manage(db=db, user_id=current_user.id, clinic_id=clinic_id)
    dso_role = None

    if clinic.dso_id is not None:
        dso_role = get_dso_role(db, current_user.id, clinic.dso_id)
    clinic_role = get_clinic_role(db, current_user.id, clinic_id)

    is_manager_actor = (dso_role is not None and dso_role.role == RoleType.MANAGER) or (clinic_role is not None and clinic_role.role == RoleType.MANAGER)

    if is_manager_actor and payload.role =="manager":
        raise HTTPException(status_code = status.HTTP_403_FORBIDDEN, detail= "Manger can only invite staff")
    now = _now()
    email = payload.email.lower().strip()

    old = (db.query(MemberInvite).filter(MemberInvite.email == email, MemberInvite.scope_type == ScopeType.CLINIC, MemberInvite.clinic_id == clinic_id, MemberInvite.accepted_at.is_(None), MemberInvite.revoked_at.is_(None))).all()

    for row in old:
        row.revoked_at = now 

    raw_token = secrets.token_urlsafe(48)

    invite = MemberInvite(
        email=email,
        token_hash= hash_token(raw_token),
        scope_type=ScopeType.CLINIC,
        dso_id=clinic.dso_id,  # keeps context if clinic belongs to a DSO
        clinic_id=clinic_id,
        role=RoleType(payload.role),
        created_by=current_user.id,
        expires_at=now + timedelta(hours=INVITE_TTL_HOURS),
    )

    try:
        logger.info(
        "Clinic invite created",
        extra={"actor_user_id": current_user.id, "email": email, "role": payload.role},
        )
        db.add(invite)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to create clinic invite")

    return {
        "message": "Clinic invite created",
        "invite_token": raw_token,  # send by email in production
        "expires_at": invite.expires_at.isoformat(),
    }


@router.post("/accept", status_code=status.HTTP_201_CREATED)
async def accept_invite(
    payload: accept_invite_request,
    current_user: Users = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now = _now()
    token_hash = hash_token(payload.token)

    invite = (
        db.query(MemberInvite)
        .filter(
            MemberInvite.token_hash == token_hash,
            MemberInvite.accepted_at.is_(None),
            MemberInvite.revoked_at.is_(None),
        )
        .first()
    )

    if  invite is None:
        logger.warning(
        "Invite accept rejected: invalid invite token",
        extra={"user_id": current_user.id},
         )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite token")
    expires_at = invite.expires_at

    if expires_at is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite token expired")
    
    if expires_at <= now: #type: ignore
         raise HTTPException(status_code=status.HTTP_410_GONE, detail="Invite token expired") 

    if invite.email.lower().strip() != current_user.email.lower().strip():
        logger.warning(
        "Invite accept rejected: email mismatch",
        extra={"user_id": current_user.id, "invite_id": invite.id, "scope_type": invite.scope_type.value},
         )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invite email mismatch")

    # avoid duplicate active assignment
    existing = (
        db.query(RoleAssignment)
        .filter(
            RoleAssignment.user_id == current_user.id,
            RoleAssignment.scope_type == invite.scope_type,
            RoleAssignment.dso_id == invite.dso_id,
            RoleAssignment.clinic_id == invite.clinic_id,
            RoleAssignment.is_active == True,
        )
        .first()
    )
    if existing:
        invite.accepted_at = now
        db.commit()
        return {"message": "Already a member in this scope"}

    assignment = RoleAssignment(
        user_id=current_user.id,       # invited user
        scope_type=invite.scope_type,
        dso_id=invite.dso_id,
        clinic_id=invite.clinic_id,
        role=invite.role,
        created_by=invite.created_by,  # inviter
        is_active=True,
    )

    try:
        db.add(assignment)
        invite.accepted_at = now
        db.commit()
    except SQLAlchemyError:
        logger.exception(
        "Invite accept failed: database error",
        extra={"user_id": current_user.id},
        )
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to accept invite")

    return {"message": "Invite accepted successfully"}
