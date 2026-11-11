import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import cliniccreate, clinicout
from app.security.oauth2 import get_current_user
from app.security.rbac import require_dso_manage
from app.services.registration import get_dso, register_dso_clinic, register_standalone_clinic

log = logging.getLogger(__name__)

router = APIRouter(
    prefix = "/clinics",
    tags= ["Registsration"]
    )


@router.post("/",  status_code = status.HTTP_201_CREATED, response_model= clinicout)
async def standalone_clinic(payload : cliniccreate,  request: Request, db: AsyncSession = Depends(get_db), current_user : Users = Depends(get_current_user)):
    return await register_standalone_clinic(
        db,
        payload=payload,
        owner_id=current_user.id,
        request_id=request.state.request_id,
    )


@router.post("/dso/{dso_id}", status_code= status.HTTP_201_CREATED, response_model= clinicout)
async def dso_clinic(dso_id : UUID , payload:cliniccreate, request: Request, db: AsyncSession = Depends(get_db), current_user : Users = Depends(get_current_user)):
    dso = await get_dso(db, dso_id=dso_id)
    await require_dso_manage(db=db, user_id=current_user.id, dso_id=dso_id)
    log.info("User authorized to create clinic in DSO",
             extra={"user_id": current_user.id, "dso_id": dso_id, "request_id": request.state.request_id})

    return await register_dso_clinic(
        db,
        dso=dso,
        payload=payload,
        actor_user_id=current_user.id,
        request_id=request.state.request_id,
    )
