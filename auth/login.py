from fastapi import Depends, APIRouter, status, HTTPException, Request, Response, Cookie, Header
from core.queue import async_redis
from  core.database import get_db
from auth.google_auth import verify_google_credentials
from auth.session_helper import start_login_session
from sqlalchemy.orm import Session
from core.models import Users
from core.schemas import loginresponse, loginrequest, refreshresponse, google_login_request
from auth.oauth2 import create_access_token, create_refresh_token ,  verify_password, validate_refresh_token, set_refresh_cookie, set_stream_access_cookie
from auth.session_helper import start_login_session
from auth.csrf_helper import verify_csrf, make_csrf_token, set_csrf_token
from auth.login_helper import handle_failed_login, login_attempts, get_redis_attempts , MAX_LOGIN_ATTEMPTS, get_client_ip, clear_attempts_with_key
import logging
import uuid

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix = "/login",
    tags = ["Auth"] 
)


@router.post("", status_code=  200,  response_model= loginresponse)
async def login(payload: loginrequest, request: Request, response: Response, db:Session= Depends(get_db)):
    email = payload.email.strip().lower()
    password = payload.password
    ip = get_client_ip(request)
    key = await login_attempts(email, ip)

    attempts = await get_redis_attempts(key)
    if attempts >= MAX_LOGIN_ATTEMPTS:
        raise HTTPException( status.HTTP_429_TOO_MANY_REQUESTS, detail = "Too many login attempt please Try again Later")
    
    user = db.query(Users).filter(Users.email == email).first()
    if  user and  verify_password(password, hashed_password = user.password):
        if  not user.is_active:
            logger.warning(
            "Login rejected: account is deactivated",
            extra={"user_id": str(user.id), "email": user.email},
            )
            raise HTTPException(status_code=401, detail="Account is deactivated")
    
        await clear_attempts_with_key(key)
        return start_login_session(user=user, response=response, db=db)

    await handle_failed_login(key)



@router.post("/google", status_code=status.HTTP_200_OK, response_model= loginresponse)
async def login_with_google(
    payload: google_login_request,
    response: Response,
    db: Session = Depends(get_db)
):
    google_user = verify_google_credentials(payload.credential)

    user = db.query(Users).filter(Users.google_sub == google_user["sub"]).first()

    if user is None:
        logger.warning(
            "Google login rejected: no Google account found",
            extra={"email": google_user["email"]},
        )
        raise HTTPException(
            status_code=404,
            detail="No Google account found. Sign up first."
        )
    
    if not user.is_active:
        logger.warning(
            "Google login rejected: account is deactivated",
            extra={"user_id": str(user.id), "email": user.email},
        )
        raise HTTPException(status_code=401, detail="Account is deactivated")

    logger.info(
        "Google login successful",
        extra={"user_id": str(user.id), "email": user.email},
    )

    return start_login_session(user=user, response=response, db=db)
        
    

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
    set_stream_access_cookie(response, new_access_token)
    new_csrf_token = make_csrf_token()
    set_csrf_token(response, new_csrf_token)
    return{
        "access_token": new_access_token,
        "csrf_token": new_csrf_token,
    }
