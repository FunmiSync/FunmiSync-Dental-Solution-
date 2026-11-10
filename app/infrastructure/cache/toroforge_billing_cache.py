import json
from uuid import UUID
from app.infrastructure.queue import redis_client
from app.infrastructure.cache.resilience import optional_cache, encode_cache_payload, decode_cache_payload


DSO_BILLING_TTL_SECONDS = 300
CLINIC__BILLING_TTL_SECONDS = 300



def dso_billing_cache_key(*, dso_id: UUID) -> str:
    return f"billing:command-center:dso:{dso_id}"


def clinic_billing_cache_key(*, clinic_id: UUID) -> str:
    return f"billing:command-center:clinic:{clinic_id}"


@optional_cache
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
    
    return decode_cache_payload(raw_text)


@optional_cache
def cache_set_json(key: str, payload:dict, ttl_seconds: int) -> None:
    redis_client.setex(key, ttl_seconds, encode_cache_payload(payload))


@optional_cache
def invalidate_dso_billing_cache(*, dso_id: UUID) -> None:
    redis_client.delete(dso_billing_cache_key(dso_id=dso_id))


@optional_cache
def invalidate_clinic_billing_cache(*, clinic_id: UUID, dso_id: UUID | None = None) -> None:
    redis_client.delete(clinic_billing_cache_key(clinic_id=clinic_id))

    if dso_id is not None:
        invalidate_dso_billing_cache(dso_id=dso_id)


def invalidate_billing_scope(*, clinic_id: UUID | None, dso_id: UUID | None) -> None:
    # Run independently so a failed clinic invalidation still attempts the DSO.
    if clinic_id is not None:
        invalidate_clinic_billing_cache(clinic_id=clinic_id)
    if dso_id is not None:
        invalidate_dso_billing_cache(dso_id=dso_id)
