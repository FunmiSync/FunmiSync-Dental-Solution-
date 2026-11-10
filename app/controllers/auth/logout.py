from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import logoutresponse
from app.security.oauth2 import get_current_user
from app.services.auth.session import end_login_session

router = APIRouter(
    prefix= "/logout",
    tags= ["Auth"]
)


@router.post("", status_code= 200)
async def logout(
    response: Response,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await end_login_session(user=current_user, response=response, db=db)
    return logoutresponse(message="You Have been Logged Out Successfully")
