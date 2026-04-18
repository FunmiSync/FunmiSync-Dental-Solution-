import json
import logging
from uuid import UUID
from redis.exceptions import RedisError
from sqlalchemy.orm import Session
from core.models import RegisteredClinics
from caches.sync_log_cache import invalidate_hot_sync_log_cache
from core.queue import redis_client

logger = logging.getLogger(__name__)

def dso_sync_logs_channel(dso_id:UUID) -> str:
    return f"sync_logs:dso:{dso_id}"

def clinic_sync_logs_channel(clinic_id:UUID) -> str:
    return f"sync_logs:clinic:{clinic_id}"


def publish_sync_log_changed(
        db:Session,
        *,
        clinic_id: UUID,
        sync_log_id: UUID,
)-> None:
    
    clinic = db.query(RegisteredClinics.id, RegisteredClinics.dso_id).filter(RegisteredClinics.id == clinic_id).first()

    if clinic is None:
        return
    
    payload = json.dumps({
        "type": "sync_logs_chnaged",
        "clinic_id": str(clinic_id),
        "sync_log_id": str(sync_log_id),
        "dso_id": str(clinic.dso_id) if clinic.dso_id else None
    })

    try:
        invalidate_hot_sync_log_cache(
            dso_id= clinic.dso_id,
            clinic_id=clinic_id
        )
        if clinic.dso_id:
            redis_client.publish(dso_sync_logs_channel(clinic.dso_id), payload)
        
        redis_client.publish(clinic_sync_logs_channel(clinic_id), payload)

    except RedisError:
         logger.exception(
            "Failed to publish sync log change event",
            extra={
                "clinic_id": str(clinic_id),
                "sync_log_id": str(sync_log_id),
            },
        )
