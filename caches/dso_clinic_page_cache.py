import json
from uuid import UUID
from core.queue import redis_client


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
        f"search:{value_part(search)}:"
        f"status:{value_part(status_filter)}:"
        f"limit:{limit}:"
        f"offset:{offset}"
    )

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
    
    return json.loads(raw_text)


def cache_set_json(key:str, payload: dict, ttl_seconds:int):
    redis_client.setex(key, ttl_seconds, json.dumps(payload))


def  invalidate_dso_clinic_list_cache(*, dso_id: UUID) -> None:
    pattern = f"dso_clinics:list:dso:{dso_id}:*"

    for key in redis_client.scan_iter(match=pattern):
        redis_client.delete(key)
