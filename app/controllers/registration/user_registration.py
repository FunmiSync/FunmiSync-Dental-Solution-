from fastapi import Depends, APIRouter, HTTPException, status, Response
from app.infrastructure.database import get_db
from app.models.entities import Users
from sqlalchemy.orm import Session
from app.security.oauth2 import hashpassword
from app.schemas.api import usercreate, userout
from app.services.auth.session import start_login_session
from app.schemas.api import loginresponse, google_register_request
from app.security.google import verify_google_credentials
from sqlalchemy.exc import SQLAlchemyError
import logging
import secrets
from app.services.registration import create_user, RegistrationConflict

logger = logging.getLogger(__name__)

router =  APIRouter(
    prefix = "/register",
    tags =["Registration"]
)
@router.post( "" , status_code= status.HTTP_201_CREATED, response_model= userout)
async def registration (payload: usercreate, db :  Session = Depends(get_db)):
    try:
        return create_user(db,username=payload.username,email=payload.email,password=payload.password)
    except RegistrationConflict as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.error("User registration failed (%s)",type(exc).__name__)
        raise HTTPException(status_code=500,detail="Unable to register user") from exc


@router.post("/google",status_code= status.HTTP_201_CREATED, response_model= loginresponse)
async def register_with_google(
        payload: google_register_request,
        response: Response,
        db: Session =  Depends(get_db)
):
    
    username = payload.username.strip()
    if not username:
        logger.warning("Google registartion rejected missing username ")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")
    
    google_user = verify_google_credentials(payload.credential)

    existing_google = db.query(Users).filter(Users.google_sub == google_user["sub"]).first()
    if existing_google:
        logger.warning(
            "Google registration rejected: Google account alreaady exists",
            extra= {
                "email": google_user["email"]
            }
        )
        raise HTTPException(
            status_code= status.HTTP_400_BAD_REQUEST,
            detail = "Google account already registered. Use login with Google."
        )
    
    existing_email = db.query(Users).filter(Users.email == google_user["email"]).first()
    if existing_email:
        logger.warning(
            "Google registration rejected: email already exists",
            extra={"email": google_user["email"]},
        )
        raise HTTPException(
            status_code=400,
            detail="Email already exists. Sign in normally ",
        )
    
    unusable_password = hashpassword(secrets.token_urlsafe(48))

    user = Users(
        username = username,
        email= google_user["email"],
        password= unusable_password,
        google_sub = google_user["sub"],
        auth_provider= "google"    
        )
    
    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Google user registration failed during database write",
            extra={"email": google_user["email"]},
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to register user",
        )

    logger.info(
    "Google user registered successfully",
    extra={"user_id": str(user.id), "email": user.email},
        )

    return start_login_session(user=user, response=response, db=db)


