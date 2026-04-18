from fastapi import APIRouter, Depends
from core.database import get_db
from sqlalchemy.orm import Session
from core.models import Users, RoleAssignment, ScopeType, Dso, RegisteredClinics
from core.schemas import my_workspaces_out, workspace_item, workspace_ref
from auth.oauth2 import get_current_user
from uuid import UUID


router = APIRouter(
    prefix= "/me",
    tags = ["Workspaces"]
    )

@router.get("/workspaces", response_model= my_workspaces_out)
async def get_my_workspaces(current_user : Users = Depends(get_current_user), db: Session = Depends(get_db)):

    workspace_map: dict[tuple[str, UUID], workspace_item] = {}

    dso_rows = db.query(RoleAssignment, Dso).join(Dso, RoleAssignment.dso_id == Dso.id).filter(RoleAssignment.user_id == current_user.id, RoleAssignment.scope_type == ScopeType.DSO, RoleAssignment.is_active.is_(True)).all()

    for assignment, dso in dso_rows:
        workspace_map[("dso", dso.id)] = workspace_item(
            scope_type="dso",
            role=assignment.role.value,
            access_source="owner" if dso.user_id == current_user.id else "dso_assignment",
            dso_id=dso.id,
            dso_name=dso.name,
            clinic_id=None,
            clinic_name=None,
        )

    clinic_rows = db.query(RoleAssignment, RegisteredClinics, Dso).join(RegisteredClinics, RoleAssignment.clinic_id == RegisteredClinics.id).outerjoin(Dso, RegisteredClinics.dso_id == Dso.id).filter(RoleAssignment.user_id == current_user.id, RoleAssignment.scope_type == ScopeType.CLINIC, RoleAssignment.is_active.is_(True)).all()

    for assignment, clinic, dso in clinic_rows:
         workspace_map[("clinic", clinic.id)] = workspace_item(
            scope_type="clinic",
            role=assignment.role.value,
            access_source="clinic_assignment",
            dso_id=clinic.dso_id,
            dso_name=dso.name if dso else None,
            clinic_id=clinic.id,
            clinic_name=clinic.clinic_name,
        )

    owned_standalone_clinics = (
        db.query(RegisteredClinics)
        .filter(
            RegisteredClinics.owner_id == current_user.id,
            RegisteredClinics.dso_id.is_(None),
        )
        .all()
    )

    for clinic in owned_standalone_clinics:
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

    default_workspace: workspace_ref | None = None
    if len(workspaces) == 1:
        only = workspaces[0]
        default_workspace = workspace_ref(
            scope_type=only.scope_type,
            dso_id=only.dso_id,
            clinic_id=only.clinic_id,
        )

    return {
        "user_id": current_user.id,
        "workspace_count": len(workspaces),
        "workspaces": workspaces,
        "default_workspace": default_workspace,
        "username": current_user.username
    }
