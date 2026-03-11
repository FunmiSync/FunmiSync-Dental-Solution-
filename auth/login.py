from fastapi import Depends, APIRouter, status, HTTPException, Request,Response, Cookie, Header
from core.queue import async_redis
from  core.database import get_db
from sqlalchemy.orm import Session
from core.models import Users
from core.schemas import loginresponse, loginrequest, refreshresponse
from auth.oauth2 import create_access_token, create_refresh_token ,  verify_password, validate_refresh_token, set_refresh_cookie
from auth.csrf_helper import verify_csrf, make_csrf_token, set_csrf_token
from infra.login_helper import handle_failed_login, login_attempts, get_redis_attempts , MAX_LOGIN_ATTEMPTS, get_client_ip, clear_attempts_with_key
import logging
import uuid

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix = "/login",
    tags = ["Auth"] 
)


@router.post("", status_code=  200,  response_model= loginresponse)
async def login(payload: loginrequest, request: Request, response: Response, db:Session= Depends(get_db)):
    email = payload.email
    password = payload.password
    ip = get_client_ip(request)
    key = await login_attempts(email, ip)

    attempts = await get_redis_attempts(key)
    if attempts >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException( status.HTTP_429_TOO_MANY_REQUESTS, detail = "Too many login attempt please Try again Later")
    
    user = db.query(Users).filter(Users.email == email).first()
    if  user and  verify_password(password, hashed_password = user.password):
        await clear_attempts_with_key(key)
        new_jti = str(uuid.uuid4())
        user.refresh_jti = new_jti
        db.commit()
        access_token = create_access_token(user = user)
        refresh_token = create_refresh_token(user = user)
        set_refresh_cookie(response, refresh_token)
        csrf_token = make_csrf_token()
        set_csrf_token(response, csrf_token)
        return {
        "access_token" : access_token,
             }
    await handle_failed_login(key)




#Api to get the new refresh token 
@router.post("/refresh", status_code= 200, response_model=refreshresponse)
async def refresh_access_token(response: Response, request: Request, db:Session = Depends(get_db), refresh_token: str | None= Cookie(default=None), csrf_cookie: str| None = Cookie(default= None, alias= "csrf_token"), csrf_header : str | None = Header(default= None, alias= "X-CSRF-Token")):

    verify_csrf(csrf_cookie, csrf_header, request)

    if not refresh_token:
        logger.warning(
        "Refresh denied: missing refresh token",
         extra={"request_id": request.state.request_id, "path": request.url.path},
        )
        raise HTTPException(status_code= status.HTTP_401_UNAUTHORIZED, detail = "Missing refreshtoken")
    

    user = validate_refresh_token(refresh_token, db)
    next_jti = str(uuid.uuid4())
    user.refresh_jti = next_jti
    db.commit()

    new_access_token = create_access_token(user = user)
    new_refresh_token = create_refresh_token(user = user)
    set_refresh_cookie(response, new_refresh_token)
    return{
        "access_token": new_access_token
    }
