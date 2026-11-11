from fastapi import status, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import RoleAssignment, ScopeType, RoleType, RegisteredClinics
import logging
from uuid import UUID

logger = logging.getLogger(__name__)


async def get_dso_role(db: AsyncSession, user_id: UUID, dso_id: UUID):
    return await db.scalar(select(RoleAssignment).where(
        RoleAssignment.user_id == user_id,
        RoleAssignment.scope_type == ScopeType.DSO,
        RoleAssignment.dso_id == dso_id,
        RoleAssignment.is_active.is_(True),
    ).limit(1))


async def get_clinic_role(db: AsyncSession, user_id: UUID, clinic_id: UUID):
    return await db.scalar(select(RoleAssignment).where(
        RoleAssignment.user_id == user_id,
        RoleAssignment.scope_type == ScopeType.CLINIC,
        RoleAssignment.clinic_id == clinic_id,
        RoleAssignment.is_active.is_(True),
    ).limit(1))


async def _get_clinic(db: AsyncSession, clinic_id: UUID) -> RegisteredClinics:
    clinic = await db.scalar(select(RegisteredClinics).where(RegisteredClinics.id == clinic_id).limit(1))
    if not clinic:
        logger.warning("Clinic Not Found", extra={"Clinic": clinic_id})
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Clinic Not Found")
    return clinic


async def require_dso_access(db: AsyncSession, user_id: UUID, dso_id: UUID):
    role = await get_dso_role(db, user_id, dso_id)
    if not role:
        logger.warning("No access to this DSO", extra={
            "dso": dso_id,
            "user": user_id
        })
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No access to this DSO")
    return role


async def require_dso_manage(db: AsyncSession, user_id: UUID, dso_id: UUID):
    role = await require_dso_access(db, user_id, dso_id)
    if role.role not in {RoleType.ADMIN, RoleType.MANAGER}:
        logger.warning("Unauthorized access", extra={
            "dso": dso_id,
            "user": user_id
        })
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not allowed for this DSO")
    return role


async def require_clinic_access(db: AsyncSession, user_id: UUID, clinic_id: UUID):
    clinic = await _get_clinic(db, clinic_id)

    if clinic.dso_id is None and clinic.owner_id == user_id:
        return clinic

    # A clinic-level membership unlocks only that clinic, even inside a DSO.
    if await get_clinic_role(db, user_id, clinic_id):
        return clinic

    logger.warning("No access to this clinic", extra={
        "Clinic": clinic_id,
        "user": user_id
    })
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No access to this clinic")


async def require_clinic_manage(db: AsyncSession, user_id: UUID, clinic_id: UUID):
    clinic = await _get_clinic(db, clinic_id)

    if clinic.dso_id is not None and clinic.owner_id == user_id:
        return clinic

    clinic_role = await get_clinic_role(db, user_id, clinic_id)
    if clinic_role and clinic_role.role in {RoleType.ADMIN, RoleType.MANAGER}:
        return clinic

    logger.warning("No manage permission for clinic", extra={
        "Clinic": clinic_id,
        "user": user_id
    })
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not allowed for this clinic")
