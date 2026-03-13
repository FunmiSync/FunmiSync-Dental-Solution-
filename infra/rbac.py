from fastapi import status, HTTPException
from core.models import RoleAssignment, ScopeType, RoleType, RegisteredClinics
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

def get_dso_role(db, user_id: UUID, dso_id: UUID):
    return(
        db.query(RoleAssignment).filter(RoleAssignment.user_id ==user_id, RoleAssignment.scope_type == ScopeType.DSO, RoleAssignment.dso_id ==dso_id, RoleAssignment.is_active ==True).first()
    )

def get_clinic_role(db, user_id: UUID, clinic_id: UUID):
    return (
        db.query(RoleAssignment).filter(
            RoleAssignment.user_id == user_id,
            RoleAssignment.scope_type == ScopeType.CLINIC,
            RoleAssignment.clinic_id == clinic_id,
            RoleAssignment.is_active == True
        ). first()
            )

def require_dso_access(db, user_id: UUID, dso_id: UUID):
    role = get_dso_role(db, user_id, dso_id)
    if not role:
        logger.warning("No access to this DSO", extra={
            "dso": dso_id,
            "user": user_id
        })
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No access to this DSO")
    return role

def require_dso_manage(db, user_id: UUID, dso_id: UUID):
    role = require_dso_access(db, user_id, dso_id)
    if role.role not in {RoleType.ADMIN , RoleType.MANAGER}:
        logger.warning("Unauthorized access", extra= {
            "dso": dso_id,
            "user": user_id
        })
        raise HTTPException(status.HTTP_403_FORBIDDEN,detail = "Not allowed for this DSO")
    return role

def require_clinic_access(db, user_id: UUID, clinic_id: UUID):
    clinic = db.query(RegisteredClinics).filter(RegisteredClinics.id == clinic_id).first()
    if not clinic:
        logger.warning("Clinic Not Found", extra= {
            "Clinic": clinic_id
        })
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail= "Clinic Not Found")

    # A DSO-level membership unlocks all clinics under that DSO.
    if clinic.dso_id:
        dso_role = get_dso_role(db, user_id, clinic.dso_id)
        if dso_role:
            return clinic

    # A clinic-level membership unlocks only that clinic, even inside a DSO.
    clinic_role = get_clinic_role(db, user_id, clinic_id)
    if clinic_role:
        return clinic

    logger.warning("No access to this clinic", extra= {
            "Clinic": clinic_id,
            "user": user_id
        })
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail= "No access to this clinic")
    


def require_clinic_manage(db, user_id: UUID, clinic_id: UUID):
    clinic = db.query(RegisteredClinics).filter(RegisteredClinics.id == clinic_id).first()
    if not clinic:
        logger.warning("Clinic Not Found", extra={
            "Clinic": clinic_id
        })
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Clinic Not Found")

    if clinic.dso_id:
        dso_role = get_dso_role(db, user_id, clinic.dso_id)
        if dso_role and dso_role.role in {RoleType.ADMIN, RoleType.MANAGER}:
            return clinic

    clinic_role = get_clinic_role(db, user_id, clinic_id)
    if clinic_role and clinic_role.role in {RoleType.ADMIN, RoleType.MANAGER}:
        return clinic

    logger.warning("No manage permission for clinic", extra={
        "Clinic": clinic_id,
        "user": user_id
    })
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not allowed for this clinic")
