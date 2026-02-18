from fastapi import Depends, APIRouter, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from core.schemas import registerdso, dsoout
from auth.oauth2 import get_current_user
from core.database import get_db
from core.models import Dso
from core.models import Users
import logging 

log = logging.getLogger(__name__)

router = APIRouter(
    prefix= "/DSO",
    tags = ["Registration"]
)


@router.post("/", status_code=status.HTTP_201_CREATED, response_model= dsoout)
async def registerDso(payload : registerdso , request: Request, current_user : Users = Depends(get_current_user), db: Session = Depends(get_db)):
    name = payload.name
    existing = db.query(Dso).filter(Dso.user_id == current_user.id).first()
    if existing:
        log.info("Duplicate creation of Dso", extra={
            "user_id" : current_user.id,
            "request_id" : request.state.request_id
        })
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail = "This Dso already exist")
    
    dso = Dso(
        name = name,
        user_id = current_user.id
    )
    
    try:
        db.add(dso)
        db.commit()
        db.refresh(dso)
        log.info(" DSO Account has been successfully created", extra = { 
            "user_id" : current_user.id,
            "dso_name" : dso.name
            })
    except SQLAlchemyError:
        db.rollback()
        log.exception("Database Error while creating DSO", extra = {
            "user_id" : current_user.id,
            "name" : name 
        })
        raise HTTPException(status_code = status.HTTP_500_INTERNAL_SERVER_ERROR, detail= "  Couldn't register dso internal server error ")

    return dso
