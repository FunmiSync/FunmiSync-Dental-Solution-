from fastapi import Depends, APIRouter, HTTPException, status
from core.database import get_db
from core.models import Users
from sqlalchemy.orm import Session
from auth.oauth2 import hashpassword
from core.schemas import usercreate, userout


router =  APIRouter(
    prefix = "/register",
    tags =["Registration"]
)
@router.post( "/" , status_code= status.HTTP_201_CREATED, response_model= userout)
async def registration (payload: usercreate, db :  Session = Depends(get_db)):
    email = payload.email
    existing = db.query(Users).filter(Users.email == email ).first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail = "User with this email already exist")
    
    hashed_pw = hashpassword(payload.password)

    user = Users(
        username = payload.username,
        email = payload.email,
        password = hashed_pw
    )

    db.add(user)
    db.commit()
    db.refresh(user)
    
    return user