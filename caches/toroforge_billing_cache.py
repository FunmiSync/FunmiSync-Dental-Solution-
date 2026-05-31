import json
from uuid import UUID
from core.queue import redis_client


DSO_BILLING_TTL_SECONDS = 300
CLINIC__BILLING_TTL_SECONDS = 300



def dso_billing_cache_key(*, dso_id: UUID) -> str:
    return f"billing:command-center:dso:{dso_id}"


def clinic_billing_cache_key(*, clinic_id: UUID) -> str:
    return f"billing:command-center:clinic:{clinic_id}"


def cache_get_json(key: str)-> dict | None:
    raw = redis_client.get(key)
    if not raw:
        return None
    
    if isinstance(raw, (bytes, bytearray)):
        raw_text = raw.decode("utf-8")
    elif isinstance(raw, str):
        raw_text = raw 
    else:
        return None
    
    return json.loads(raw_text)


def cache_set_json(key: str, payload:dict, ttl_seconds: int) -> None:
    redis_client.setex(key, ttl_seconds, json.dumps(payload))


def invalidate_dso_billing_cache(*, dso_id: UUID) -> None:
    redis_client.delete(dso_billing_cache_key(dso_id=dso_id))


def invalidate_clinic_billing_cache(*, clinic_id: UUID, dso_id: UUID | None = None) -> None:
    redis_client.delete(clinic_billing_cache_key(clinic_id=clinic_id))

    if dso_id is not None:
        invalidate_dso_billing_cache(dso_id=dso_id)
