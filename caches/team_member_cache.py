import json
from uuid import  UUID
from core.queue import redis_client


TEAM_MEMBERS_TTL_SECONDS = 300


def dso_team_members_cache_key(*, dso_id: UUID) -> str:
    return f"team_members:dso:{dso_id}"


def clinic_team_members_cache_key(*, clinic_id: UUID)-> str:
    return f"team_members:clinic:{clinic_id}"


def  cache_set_json(key: str, payload: dict, ttl_seconds: int)-> None:
    redis_client.setex(key, ttl_seconds, json.dumps(payload))


def  cache_get_json(key: str)-> dict | None:
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

def invalidate_dso_team_members_cache(*, dso_id: UUID) -> None:
    redis_client.delete(dso_team_members_cache_key(dso_id=dso_id))


def invalidate_clinic_team_members_cache(*, clinic_id: UUID) -> None:
    redis_client.delete(clinic_team_members_cache_key(clinic_id=clinic_id))
    