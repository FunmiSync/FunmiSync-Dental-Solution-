"""Issue and accept DSO/clinic membership invites."""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.infrastructure.cache.team_member_cache import invalidate_clinic_team_members_cache, invalidate_dso_team_members_cache
from app.models.entities import MemberInvite, RegisteredClinics, RoleAssignment, RoleType, ScopeType, Users
from app.security.rbac import get_clinic_role, get_dso_role
from app.services.audit_logs import record_audit
from app.services.errors import Gone, NotFound, OperationFailed, PermissionDenied

logger = logging.getLogger(__name__)

INVITE_TTL_HOURS = settings.invite_ttl_hours


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw_token: str) -> str:
    # Only the hash is stored, so a database leak does not expose usable tokens.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --------------------------------------------------------------- create

async def create_dso_invite(db: AsyncSession, *, dso_id: UUID, actor: RoleAssignment, email: str, role: str, created_by: UUID) -> dict:
    if actor.role == RoleType.MANAGER and role == "manager":
        logger.warning("Manager can only add staff", extra={"user": created_by, "role": actor.role})
        raise PermissionDenied("Manager can only invite staff")

    email = email.lower().strip()
    raw_token, invite = await _issue_invite(
        db,
        email=email,
        scope_type=ScopeType.DSO,
        scope_filter=MemberInvite.dso_id == dso_id,
        dso_id=dso_id,
        clinic_id=None,
        role=role,
        created_by=created_by,
        failure_detail="Failed o create Dso invite",
    )
    logger.info("DSO invite created", extra={"actor_user_id": created_by, "email": email, "dso_id": dso_id, "role": role})
    return {
        "message": "Dso invite created",
        "invite_token": raw_token,
        "expires_at": invite.expires_at.isoformat(),
    }


async def create_clinic_invite(db: AsyncSession, *, clinic: RegisteredClinics, email: str, role: str, created_by: UUID) -> dict:
    if role == "manager" and await _is_manager(db, user_id=created_by, clinic=clinic):
        raise PermissionDenied("Manger can only invite staff")

    email = email.lower().strip()
    raw_token, invite = await _issue_invite(
        db,
        email=email,
        scope_type=ScopeType.CLINIC,
        scope_filter=MemberInvite.clinic_id == clinic.id,
        dso_id=clinic.dso_id,  # keeps DSO context for clinics inside a DSO
        clinic_id=clinic.id,
        role=role,
        created_by=created_by,
        failure_detail="Failed to create clinic invite",
    )
    logger.info("Clinic invite created", extra={"actor_user_id": created_by, "email": email, "role": role})
    return {
        "message": "Clinic invite created",
        "invite_token": raw_token,  # send by email in production
        "expires_at": invite.expires_at.isoformat(),
    }


async def _is_manager(db: AsyncSession, *, user_id: UUID, clinic: RegisteredClinics) -> bool:
    # A manager at either the DSO or the clinic level may invite staff only.
    dso_role = await get_dso_role(db, user_id, clinic.dso_id) if clinic.dso_id is not None else None
    clinic_role = await get_clinic_role(db, user_id, clinic.id)
    return any(row is not None and row.role == RoleType.MANAGER for row in (dso_role, clinic_role))


async def _issue_invite(db: AsyncSession, *, email: str, scope_type: ScopeType, scope_filter, dso_id: UUID | None,
                  clinic_id: UUID | None, role: str, created_by: UUID, failure_detail: str) -> tuple[str, MemberInvite]:
    now = _now()

    # Issuing a new invite cancels any earlier pending invite for the same person and scope.
    pending = (await db.scalars(select(MemberInvite).where(
        MemberInvite.email == email,
        MemberInvite.scope_type == scope_type,
        scope_filter,
        MemberInvite.accepted_at.is_(None),
        MemberInvite.revoked_at.is_(None),
    ))).all()
    for row in pending:
        row.revoked_at = now

    raw_token = secrets.token_urlsafe(48)
    invite = MemberInvite(
        email=email,
        token_hash=hash_token(raw_token),
        scope_type=scope_type,
        dso_id=dso_id,
        clinic_id=clinic_id,
        role=RoleType(role),
        created_by=created_by,
        expires_at=now + timedelta(hours=INVITE_TTL_HOURS),
    )

    try:
        db.add(invite)
        await db.flush()
        record_audit(db, clinic_id=invite.clinic_id, entity_type="invite", entity_id=invite.id, action="create",
                     details={"actor_user_id": str(created_by)})
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Invite creation failed: database error", extra={"user_id": created_by})
        raise OperationFailed(failure_detail) from exc

    return raw_token, invite


# --------------------------------------------------------------- accept

async def accept_invite(db: AsyncSession, *, token: str, user: Users) -> dict:
    now = _now()

    # Lock the invite row so two concurrent accepts cannot both create memberships.
    invite = await db.scalar(
        select(MemberInvite)
        .where(
            MemberInvite.token_hash == hash_token(token),
            MemberInvite.accepted_at.is_(None),
            MemberInvite.revoked_at.is_(None),
        )
        .with_for_update().execution_options(populate_existing=True).limit(1)
    )

    if invite is None:
        logger.warning("Invite accept rejected: invalid invite token", extra={"user_id": user.id})
        raise NotFound("Invalid invite token")

    if invite.expires_at is None or invite.expires_at <= now:
        raise Gone("Invite token expired")

    if invite.email.lower().strip() != user.email.lower().strip():
        logger.warning("Invite accept rejected: email mismatch",
                       extra={"user_id": user.id, "invite_id": invite.id, "scope_type": invite.scope_type.value})
        raise PermissionDenied("Invite email mismatch")

    if await _has_active_membership(db, user_id=user.id, invite=invite):
        invite.accepted_at = now
        _audit_accept(db, invite, user)
        await db.commit()
        return {"message": "Already a member in this scope"}

    try:
        db.add(RoleAssignment(
            user_id=user.id,                # invited user
            scope_type=invite.scope_type,
            dso_id=invite.dso_id,
            clinic_id=invite.clinic_id,
            role=invite.role,
            created_by=invite.created_by,   # inviter
            is_active=True,
        ))
        invite.accepted_at = now
        _audit_accept(db, invite, user)
        await db.commit()
    except SQLAlchemyError as exc:
        logger.exception("Invite accept failed: database error", extra={"user_id": user.id})
        await db.rollback()
        raise OperationFailed("Failed to accept invite") from exc

    await _invalidate_team_cache(invite)
    return {"message": "Invite accepted successfully"}


async def _has_active_membership(db: AsyncSession, *, user_id: UUID, invite: MemberInvite) -> bool:
    return await db.scalar(select(RoleAssignment.id).where(
        RoleAssignment.user_id == user_id,
        RoleAssignment.scope_type == invite.scope_type,
        RoleAssignment.dso_id == invite.dso_id,
        RoleAssignment.clinic_id == invite.clinic_id,
        RoleAssignment.is_active.is_(True),
    ).limit(1)) is not None


def _audit_accept(db: AsyncSession, invite: MemberInvite, user: Users) -> None:
    record_audit(db, clinic_id=invite.clinic_id, entity_type="invite", entity_id=invite.id, action="accept",
                 details={"actor_user_id": str(user.id)})


async def _invalidate_team_cache(invite: MemberInvite) -> None:
    if invite.scope_type == ScopeType.DSO and invite.dso_id is not None:
        await invalidate_dso_team_members_cache(dso_id=cast(UUID, invite.dso_id))
    if invite.scope_type == ScopeType.CLINIC and invite.clinic_id is not None:
        await invalidate_clinic_team_members_cache(clinic_id=cast(UUID, invite.clinic_id))
