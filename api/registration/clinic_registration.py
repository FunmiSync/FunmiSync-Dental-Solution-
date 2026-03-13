from fastapi import Depends, APIRouter, status, HTTPException, Request
from sqlalchemy.orm import Session
from core.database import get_db
from  core.schemas import cliniccreate
from auth.oauth2 import get_current_user
from core.models import Users, RegisteredClinics, Dso, RoleAssignment, ScopeType, RoleType
from  auth.security import encrypt_secret
from infra.rbac import require_dso_manage
import logging
import secrets
from sqlalchemy.exc import SQLAlchemyError
from core.schemas import clinicout
from uuid import UUID

log = logging.getLogger(__name__)

router = APIRouter(
    prefix = "/clinics",
    tags= ["Registsration"]
    )

@router.post("/",  status_code = status.HTTP_201_CREATED, response_model= clinicout)
async def standalone_clinic(payload : cliniccreate,  request: Request, db: Session = Depends(get_db), current_user : Users = Depends(get_current_user)):
    raw_webhook_secret = secrets.token_urlsafe(32)
    crm_type = payload.crm_type
    clinic_name  = payload.clinic_name
    clinic_number = payload.clinic_number
    clinic_timezone = payload.clinic_timezone
    od_developer_key = payload.od_developer_key
    od_customer_key = payload.od_customer_key
    crm_api_key  = payload.crm_api_key 
    location_id = payload.location_id 
    calendar_id = payload.calendar_id 
    operatory_calendar_map  =  {
    status: [item.model_dump() for item in items]
    for status, items in payload.operatory_calendar_map.items()
}

    existing = db.query(RegisteredClinics).filter(RegisteredClinics.owner_id == current_user.id, RegisteredClinics.clinic_name == payload.clinic_name, RegisteredClinics.dso_id.is_(None)). first()
    if existing:
        log.warning("Duplicate clinic creation attempt", extra={
            "user_id" : current_user.id,   
            "clinic_name" : payload.clinic_name,
            "request_id ": request.state.request_id
        },)
        raise HTTPException(status_code = status.HTTP_400_BAD_REQUEST, detail = "You Already have a clinic created already")
    
    clinic = RegisteredClinics(
        crm_type = crm_type,
        clinic_name = clinic_name ,
        clinic_number = clinic_number,
        clinic_timezone = clinic_timezone,
        od_developer_key = encrypt_secret (od_developer_key),
        od_customer_key = encrypt_secret(od_customer_key), 
        crm_api_key = encrypt_secret(crm_api_key),
        webhook_secret = encrypt_secret(raw_webhook_secret),
        location_id = location_id,
        calendar_id = encrypt_secret(calendar_id) ,
        operatory_calendar_map = operatory_calendar_map,
        owner_id = current_user.id,
        )
    try:
        db.add(clinic)
        db.flush()

        assignment = RoleAssignment(
            user_id=current_user.id,
            scope_type=ScopeType.CLINIC,
            clinic_id=clinic.id,
            role=RoleType.ADMIN,
            created_by=current_user.id,
        )
        db.add(assignment)
        db.commit()
        db.refresh(clinic)

        log.info("Account has been successfully created", extra = { 
            "user_id" : current_user.id,
            "clinic_name" : clinic.clinic_name,
            "owner_id" : current_user.id,
            })
    except SQLAlchemyError:
        db.rollback()
        log.exception("Database error while creating clinic ", extra= {
            "user_id" : current_user.id,
            "request_id" : request.state.request_id,
            "clinic_name" : payload.clinic_name

            })
        raise HTTPException(status_code = status.HTTP_500_INTERNAL_SERVER_ERROR, detail = "Unable to create clinic at this time")

    return clinic 





@router.post("/dso/{dso_id}", status_code= status.HTTP_201_CREATED, response_model= clinicout)
async def dso_clinic(dso_id : UUID , payload:cliniccreate, request: Request, db: Session = Depends(get_db), current_user : Users = Depends(get_current_user)):

    raw_webhook_secret = secrets.token_urlsafe(32)
    crm_type = payload.crm_type
    clinic_name  = payload.clinic_name
    clinic_number = payload.clinic_number
    clinic_timezone = payload.clinic_timezone
    od_developer_key = payload.od_developer_key
    od_customer_key = payload.od_customer_key
    crm_api_key  = payload.crm_api_key 
    location_id = payload.location_id 
    calendar_id = payload.calendar_id 
    operatory_calendar_map  =  {
    status: [item.model_dump() for item in items]
    for status, items in payload.operatory_calendar_map.items()
}

    dso = db.query(Dso).filter(Dso.id == dso_id).first()
    if not dso:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dso Not Found"
        )

    require_dso_manage(db=db, user_id = current_user.id, dso_id=dso_id)
    log.info("User authorized to create clinic in DSO", extra={
        "user_id": current_user.id,
        "dso_id": dso_id,
        "request_id": request.state.request_id,
    })
    existing = db.query(RegisteredClinics).filter(RegisteredClinics.owner_id == current_user.id, RegisteredClinics.dso_id == dso_id, RegisteredClinics.clinic_number == clinic_number ). first()

    if existing:
        raise HTTPException(status_code = status.HTTP_400_BAD_REQUEST, detail = "Clinic already exist in DSO")
    
    clinic = RegisteredClinics(
        crm_type = crm_type,
        clinic_name = clinic_name ,
        clinic_number = clinic_number,
        clinic_timezone = clinic_timezone,
        od_developer_key = encrypt_secret (od_developer_key),
        od_customer_key = encrypt_secret(od_customer_key), 
        crm_api_key = encrypt_secret(crm_api_key),
        webhook_secret = encrypt_secret(raw_webhook_secret),
        location_id = location_id,
        calendar_id = encrypt_secret(calendar_id) ,
        operatory_calendar_map = operatory_calendar_map,
        owner_id = dso.user_id,
        dso_id = dso_id
        )
    
    try:
        db.add(clinic)
        db.commit()
        db.refresh(clinic)

        log.info("Clinic  has been successfully created", extra ={ 
            "user_id" : current_user.id,
            "clinic_name" : clinic.clinic_name,
            "dso_id" : dso_id,
            "owner_id" : current_user.id
            })
        
    except SQLAlchemyError:
        db.rollback()
        log.exception("Database error while creating clinic ", extra= {
            "user_id" : current_user.id,
            "request_id" : request.state.request_id,
            "clinic_name" : payload.clinic_name,
            "dso_id" : dso_id

            })
        raise HTTPException(status_code = status.HTTP_500_INTERNAL_SERVER_ERROR, detail = "Unable to create clinic at this time")

    return clinic 
