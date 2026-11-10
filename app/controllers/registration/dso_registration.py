from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import dsoout, registerdso
from app.security.oauth2 import get_current_user
from app.services.registration import create_dso

router = APIRouter(
    prefix= "/DSO",
    tags = ["Registration"]
)


@router.post("/", status_code=status.HTTP_201_CREATED, response_model= dsoout)
async def registerDso(payload : registerdso , request: Request, current_user : Users = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await create_dso(db, user_id=current_user.id, name=payload.name)
