import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.schemas.api import google_register_request, loginresponse, usercreate, userout
from app.security.google import verify_google_credentials
from app.services.auth.session import start_login_session
from app.services.registration import create_google_user, create_user

logger = logging.getLogger(__name__)

router =  APIRouter(
    prefix = "/register",
    tags =["Registration"]
)


# bcrypt hashing and the Google token check run in a worker thread inside the
# services they call, so these async handlers never block the event loop.
@router.post( "" , status_code= status.HTTP_201_CREATED, response_model= userout)
async def registration (payload: usercreate, db :  AsyncSession = Depends(get_db)):
    return await create_user(db, username=payload.username, email=payload.email, password=payload.password)


@router.post("/google",status_code= status.HTTP_201_CREATED, response_model= loginresponse)
async def register_with_google(
        payload: google_register_request,
        response: Response,
        db: AsyncSession =  Depends(get_db)
):
    username = payload.username.strip()
    if not username:
        logger.warning("Google registration rejected: missing username")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username is required")

    google_user = await verify_google_credentials(payload.credential)
    user = await create_google_user(db, username=username, google_user=google_user)
    return await start_login_session(user=user, response=response, db=db)
