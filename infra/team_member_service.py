import logging
from datetime import datetime, timezone
from typing import cast
from uuid import UUID
from sqlalchemy.orm import Session
from core.models import RoleAssignment, ScopeType, Users
from core.schemas import team_member_list_out, team_member_row_out
from caches.team_member_cache import  TEAM_MEMBERS_TTL_SECONDS, cache_get_json, cache_set_json, clinic_team_members_cache_key, dso_team_members_cache_key



logger = logger = logging.getLogger(__name__)



def to_member_row(*, assignment: RoleAssignment, user: Users)-> team_member_row_out:
    return team_member_row_out(
        user_id= user.id,
        email= cast(str, user.email),
        role= assignment.role.value,
        scope= assignment.scope_type.value,
        joined_at= assignment
    )


def build_dso_team_members_cached(
        db: Session,
        *,
        dso_id: UUID
)-> team_member_list_out:

    cache_key = dso_team_members_cache_key(dso_id=dso_id)
    cached = cache_get_json(cache_key)


    if cached:
        logger.info(
            "DSO team members cache hit",
            extra={"dso_id": str(dso_id)},
        )
        return team_member_list_out(**cached)
    
    rows = (
        db.query(RoleAssignment, Users).join(Users, Users.id == RoleAssignment.user_id).filter(
            Users.is_active.is_(True),
            RoleAssignment.is_active.is_(True),
            RoleAssignment.scope_type == ScopeType.DSO,
            RoleAssignment.dso_id ==dso_id
        ).order_by(RoleAssignment.created_at.asc()).all()
    )

    items =[
        to_member_row(assignment=assignment, user=user)
        for assignment,user in rows
    ]

    response = team_member_list_out(
        generated_at= datetime.now(timezone.utc),
        active_count= len(items),
        items= items
    )

    cache_set_json(
        cache_key,
        response.model_dump(mode="json"),
        TEAM_MEMBERS_TTL_SECONDS
    )

    return response 




def build_clinic_team_members_cached(
        db: Session,
        *,
        clinic_id: UUID,
) -> team_member_list_out:
    
    cache_key = clinic_team_members_cache_key(clinic_id= clinic_id)
    cached = cache_get_json(cache_key)

    if cached:
        logger.info(
            "Clinic team members cache hit",
            extra={"clinic_id": str(clinic_id)},
        )
        return team_member_list_out(**cached)
    
    rows = (
        db.query(RoleAssignment, Users).join(Users, Users.id == RoleAssignment.user_id).filter(
            Users.is_active.is_(True),
            RoleAssignment.is_active.is_(True),
            RoleAssignment.scope_type == ScopeType.CLINIC,
            RoleAssignment.clinic_id == clinic_id,
        ).order_by(RoleAssignment.created_at.asc()).all()
    )


    items = [
        to_member_row(assignment=assignment, user=user)
        for assignment, user in rows
    ]

    response = team_member_list_out(
        generated_at=datetime.now(timezone.utc),
        active_count=len(items),
        items=items,
    )

    cache_set_json(
        cache_key,
        response.model_dump(mode="json"),
        TEAM_MEMBERS_TTL_SECONDS,
    )

    return response
