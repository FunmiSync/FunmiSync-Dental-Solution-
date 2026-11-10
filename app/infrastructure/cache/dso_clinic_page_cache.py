from app.security.encryption import hash_lookup
from app.infrastructure.cache.resilience import optional_cache, encode_cache_payload, decode_cache_payload
import json
from uuid import UUID
from app.infrastructure.queue import redis_client


DSO_CLINIC_LIST_TTL_SECONDS = 300


def value_part(value:object | None) -> str:
    return str(value) if value is not None else "none"


def dso_clinic_list_cache_Key(
        *,
        dso_id: UUID,
        user_id: UUID,
        search: str | None,
        status_filter: str | None,
        limit:int,
        offset: int
)-> str:
    return (
        f"dso_clinics:list:dso:{dso_id}:"
        f"user:{user_id}:" 
        f"search:{hash_lookup(search) if search else 'none'}:"
        f"status:{value_part(status_filter)}:"
        f"limit:{limit}:"
        f"offset:{offset}"
    )

@optional_cache
def cache_get_json(key: str)-> dict | None:
    raw = redis_client.get(key)
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw_text = raw.decode("utf-8")
    elif isinstance (raw, str):
        raw_text = raw 
    else:
        return None
    
    return decode_cache_payload(raw_text)


@optional_cache
def cache_set_json(key:str, payload: dict, ttl_seconds:int):
    redis_client.setex(key, ttl_seconds, encode_cache_payload(payload))


@optional_cache
def invalidate_dso_clinic_list_cache(*, dso_id: UUID) -> None:
    pattern = f"dso_clinics:list:dso:{dso_id}:*"

    for key in redis_client.scan_iter(match=pattern):
        redis_client.delete(key)
