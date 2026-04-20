import uuid 
from fastapi import Response 
from sqlalchemy.orm  import Session
from auth.csrf_helper import set_csrf_token, make_csrf_token
from auth.oauth2 import (
    create_access_token, create_refresh_token, set_refresh_cookie, set_stream_access_cookie
)

from core.models import Users 
from core.schemas import loginresponse 

def start_login_session(*, user: Users, response: Response, db: Session)-> loginresponse:
    new_jti = str(uuid.uuid4())
    user.refresh_jti = new_jti
    db.commit()

    access_token = create_access_token(user=user)
    refresh_token = create_refresh_token(user=user)

    set_stream_access_cookie(response, access_token)
    set_refresh_cookie(response, refresh_token)

    csrf_token = make_csrf_token()
    set_csrf_token(response, csrf_token)

    return loginresponse(
        access_token= access_token,
        csrf_token=  csrf_token
    )