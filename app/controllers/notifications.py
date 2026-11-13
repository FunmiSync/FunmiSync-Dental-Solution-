"""In-app notifications for the signed-in user."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.models.entities import Users
from app.schemas.api import notification_out, notification_page_out, notifications_read_out
from app.security.oauth2 import get_current_user
from app.services.notification_service import list_notifications, mark_all_read, mark_notification_read

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=notification_page_out)
async def get_my_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_notifications(db, user_id=current_user.id, unread_only=unread_only, limit=limit)


@router.post("/read-all", response_model=notifications_read_out)
async def read_all_my_notifications(
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return notifications_read_out(updated=await mark_all_read(db, user_id=current_user.id))


@router.post("/{notification_id}/read", response_model=notification_out)
async def read_my_notification(
    notification_id: UUID,
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await mark_notification_read(db, user_id=current_user.id, notification_id=notification_id)
