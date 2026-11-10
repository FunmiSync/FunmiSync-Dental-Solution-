from fastapi import Depends, APIRouter, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.api import registerdso, dsoout
from app.security.oauth2 import get_current_user
from app.infrastructure.database import get_db
from app.models.entities import Dso, Users, RoleAssignment, ScopeType, RoleType
import logging
from app.services.registration import create_dso, RegistrationConflict

log = logging.getLogger(__name__)

router = APIRouter(
    prefix= "/DSO",
    tags = ["Registration"]
)


@router.post("/", status_code=status.HTTP_201_CREATED, response_model= dsoout)
async def registerDso(payload : registerdso , request: Request, current_user : Users = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return create_dso(db,user_id=current_user.id,name=payload.name)
    except RegistrationConflict as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        log.error("DSO registration failed (%s)",type(exc).__name__)
        raise HTTPException(status_code=500,detail="Unable to register DSO") from exc
