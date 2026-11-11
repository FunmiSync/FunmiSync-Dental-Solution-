from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import my_workspaces_out
from app.security.oauth2 import get_current_user
from app.services.workspace_service import build_my_workspaces

router = APIRouter(
    prefix= "/me",
    tags = ["Workspaces"]
    )


@router.get("/workspaces", response_model= my_workspaces_out)
async def get_my_workspaces(current_user : Users = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await build_my_workspaces(db, user=current_user)
