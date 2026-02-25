from datetime import datetime, timedelta
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext
from config import settings
from core.models import Users
from sqlalchemy.orm import Session
from fastapi import Depends, HTTPException, status, Response
from core.database import get_db
import logging


logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes= ["bcrypt"], deprecated = "auto")
SECRET_KEY = settings.secret_key
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
ALGORITHM = settings.algorithm
REFRESH_TOKEN_EXPIRE_DAYS = settings.refresh_token_expire_days
oauth2_scheme = OAuth2PasswordBearer(tokenUrl= "/login")

def hashpassword(password : str):
    return pwd_context.hash(password)

def verify_password(plain_password : str , hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_token(*, data: dict, expires_delta : timedelta):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode["exp"] = expire 
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm = ALGORITHM)
    return encoded_jwt

def create_access_token(*, user : Users):
    data = {
        "id" : user.id,
        "type" : "access",
        "token_version" : user.token_version
    }
    expires = timedelta(minutes= ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_token(data = data , expires_delta =  expires)

def create_refresh_token(*, user: Users):
    data = {
        "id" : user.id,
        "type" : "refresh",
        "token_version" : user.token_version, 
        "jti" : user.refresh_jti
    }
    expires = timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    return create_token(data = data , expires_delta = expires)

def decode_token(token: str ):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms= [ALGORITHM])
        return payload 
    except JWTError as e: 
        raise ValueError("invalid Token") from e 
    
def  get_current_user(token: str = Depends(oauth2_scheme), db:Session = Depends(get_db)): 
    try:
        payload = decode_token(token)
    except ValueError as  e :
        logger.warning(f"user provided invalid token{e}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED , detail = "Invalid Token")

    if payload.get("type")  !=  "access":
        logger.warning(f"invalid token type for user:{payload.get('id')} ")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "invalid Token")
    
    user_id = payload.get("id")
    token_version = payload.get("token_version")

    if user_id is None or token_version is None:
        logger.warning(f"invalid token type for user:{payload.get('id')} ")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "invalid Token")
    
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "Invalid Token")
    
    if user.token_version != token_version:
         raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "Invalid Token")
    
    return user


def validate_refresh_token(refresh_token: str , db: Session):
    try:
        payload = decode_token(refresh_token)
    except ValueError as e :
        logger.warning(f"user provided invalid token{e}")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail= "Invalid Token")
    
    if payload.get("type") != "refresh":
        logger.warning(f"invalid token type for user:{payload.get('id')} ")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "invalid Token")
    
    user_id = payload.get("id")
    token_version = payload.get("token_version")
    incoming_jti = payload.get("jti")

    if user_id is None or token_version is None:
        logger.warning(f"invalid token type for user:{payload.get('id')} ")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "invalid Token")
    
    user = db.query(Users).filter(Users.id == user_id).first()

    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "Invalid Token")
    
    if user.token_version != token_version:
         raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail = "Invalid Token")
    
    if user.refresh_jti != incoming_jti:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid Token")

    
    return user


def set_refresh_cookie(response: Response, refresh_token: str):
    response.set_cookie(
        key= "refresh_token",
        value= refresh_token,
        httponly= True,
        secure=True,
        samesite="none",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path= "/login/refresh"
    )





