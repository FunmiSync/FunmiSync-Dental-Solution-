import json
import logging
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.cache.sync_log_cache import invalidate_hot_sync_log_cache
from app.infrastructure.queue import async_redis
from app.models.entities import RegisteredClinics

logger = logging.getLogger(__name__)


def dso_sync_logs_channel(dso_id: UUID) -> str:
    return f"sync_logs:dso:{dso_id}"


def clinic_sync_logs_channel(clinic_id: UUID) -> str:
    return f"sync_logs:clinic:{clinic_id}"


async def publish_sync_log_changed(
        db: AsyncSession,
        *,
        clinic_id: UUID,
        sync_log_id: UUID,
) -> None:
    clinic = (await db.execute(
        select(RegisteredClinics.id, RegisteredClinics.dso_id).where(RegisteredClinics.id == clinic_id).limit(1)
    )).first()

    if clinic is None:
        return

    payload = json.dumps({
        "type": "sync_logs_chnaged",
        "clinic_id": str(clinic_id),
        "sync_log_id": str(sync_log_id),
        "dso_id": str(clinic.dso_id) if clinic.dso_id else None
    })

    try:
        await invalidate_hot_sync_log_cache(dso_id=clinic.dso_id, clinic_id=clinic_id)
        if clinic.dso_id:
            await async_redis.publish(dso_sync_logs_channel(clinic.dso_id), payload)
        await async_redis.publish(clinic_sync_logs_channel(clinic_id), payload)
    except (RedisError, OSError):
        logger.exception(
            "Failed to publish sync log change event",
            extra={"clinic_id": str(clinic_id), "sync_log_id": str(sync_log_id)},
        )
