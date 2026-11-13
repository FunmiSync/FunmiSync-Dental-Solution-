"""A user's in-app notifications (sync alerts for the clinics they belong to)."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import RegisteredClinics, SyncAlert, UserNotification
from app.schemas.api import notification_out, notification_page_out
from app.services.errors import NotFound


def _query(user_id: UUID):
    return (select(UserNotification, SyncAlert, RegisteredClinics.clinic_name)
            .join(SyncAlert, SyncAlert.id == UserNotification.alert_id)
            .join(RegisteredClinics, RegisteredClinics.id == SyncAlert.clinic_id)
            .where(UserNotification.user_id == user_id))


def _out(notification: UserNotification, alert: SyncAlert, clinic_name: str) -> notification_out:
    return notification_out(id=notification.id, clinic_id=alert.clinic_id, clinic_name=clinic_name,
                            problem=alert.code, message=alert.message, created_at=notification.created_at,
                            read_at=notification.read_at, resolved_at=alert.resolved_at)


async def list_notifications(db: AsyncSession, *, user_id: UUID, unread_only: bool, limit: int) -> notification_page_out:
    query = _query(user_id)
    if unread_only:
        query = query.where(UserNotification.read_at.is_(None))
    rows = (await db.execute(query.order_by(UserNotification.created_at.desc()).limit(limit))).all()
    unread = await db.scalar(select(func.count()).select_from(UserNotification).where(
        UserNotification.user_id == user_id, UserNotification.read_at.is_(None)))
    return notification_page_out(items=[_out(*row) for row in rows], unread=int(unread or 0))


async def mark_notification_read(db: AsyncSession, *, user_id: UUID, notification_id: UUID) -> notification_out:
    row = (await db.execute(_query(user_id).where(UserNotification.id == notification_id))).first()
    if row is None:
        raise NotFound("Notification not found")  # also for another user's notification
    notification = row[0]
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await db.commit()
    return _out(*row)


async def mark_all_read(db: AsyncSession, *, user_id: UUID) -> int:
    result = await db.execute(update(UserNotification)
                              .where(UserNotification.user_id == user_id, UserNotification.read_at.is_(None))
                              .values(read_at=datetime.now(timezone.utc)))
    await db.commit()
    return result.rowcount or 0
