"""List every DSO and clinic workspace a user can open."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Dso, RegisteredClinics, RoleAssignment, ScopeType, Users
from app.schemas.api import workspace_item, workspace_ref


async def build_my_workspaces(db: AsyncSession, *, user: Users) -> dict:
    # Keyed by scope so a clinic reachable two ways appears once.
    workspace_map: dict[tuple[str, UUID], workspace_item] = {}

    for assignment, dso in await _active_dso_memberships(db, user.id):
        workspace_map[("dso", dso.id)] = workspace_item(
            scope_type="dso",
            role=assignment.role.value,
            access_source="owner" if dso.user_id == user.id else "dso_assignment",
            dso_id=dso.id,
            dso_name=dso.name,
            clinic_id=None,
            clinic_name=None,
        )

    for assignment, clinic, dso in await _active_clinic_memberships(db, user.id):
        workspace_map[("clinic", clinic.id)] = workspace_item(
            scope_type="clinic",
            role=assignment.role.value,
            access_source="clinic_assignment",
            dso_id=clinic.dso_id,
            dso_name=dso.name if dso else None,
            clinic_id=clinic.id,
            clinic_name=clinic.clinic_name,
        )

    # Ownership of a standalone clinic wins over any membership row for it.
    for clinic in await _owned_standalone_clinics(db, user.id):
        workspace_map[("clinic", clinic.id)] = workspace_item(
            scope_type="clinic",
            role="admin",
            access_source="owner",
            dso_id=None,
            dso_name=None,
            clinic_id=clinic.id,
            clinic_name=clinic.clinic_name,
        )

    workspaces = sorted(
        workspace_map.values(),
        key=lambda item: (
            0 if item.scope_type == "dso" else 1,
            (item.dso_name or item.clinic_name or "").lower(),
        ),
    )

    return {
        "user_id": user.id,
        "workspace_count": len(workspaces),
        "workspaces": workspaces,
        "default_workspace": _default_workspace(workspaces),
        "username": user.username,
    }


def _default_workspace(workspaces: list[workspace_item]) -> workspace_ref | None:
    # Only pre-select when there is no choice to make.
    if len(workspaces) != 1:
        return None
    only = workspaces[0]
    return workspace_ref(scope_type=only.scope_type, dso_id=only.dso_id, clinic_id=only.clinic_id)


async def _active_dso_memberships(db: AsyncSession, user_id: UUID):
    return (await db.execute(
        select(RoleAssignment, Dso)
        .join(Dso, RoleAssignment.dso_id == Dso.id)
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.scope_type == ScopeType.DSO,
            RoleAssignment.is_active.is_(True),
        )
    )).all()


async def _active_clinic_memberships(db: AsyncSession, user_id: UUID):
    return (await db.execute(
        select(RoleAssignment, RegisteredClinics, Dso)
        .join(RegisteredClinics, RoleAssignment.clinic_id == RegisteredClinics.id)
        .outerjoin(Dso, RegisteredClinics.dso_id == Dso.id)
        .where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.scope_type == ScopeType.CLINIC,
            RoleAssignment.is_active.is_(True),
        )
    )).all()


async def _owned_standalone_clinics(db: AsyncSession, user_id: UUID):
    return (await db.scalars(
        select(RegisteredClinics)
        .where(RegisteredClinics.owner_id == user_id, RegisteredClinics.dso_id.is_(None))
    )).all()
