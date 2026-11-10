from uuid import UUID

from app.infrastructure.cache.resilience import decode_cached_text, encode_cache_payload, optional_cache
from app.infrastructure.queue import async_redis

TEAM_MEMBERS_TTL_SECONDS = 300


def dso_team_members_cache_key(*, dso_id: UUID) -> str:
    return f"team_members:dso:{dso_id}"


def clinic_team_members_cache_key(*, clinic_id: UUID) -> str:
    return f"team_members:clinic:{clinic_id}"


@optional_cache
async def cache_set_json(key: str, payload: dict, ttl_seconds: int) -> None:
    await async_redis.setex(key, ttl_seconds, encode_cache_payload(payload))


@optional_cache
async def cache_get_json(key: str) -> dict | None:
    return decode_cached_text(await async_redis.get(key))


@optional_cache
async def invalidate_dso_team_members_cache(*, dso_id: UUID) -> None:
    await async_redis.delete(dso_team_members_cache_key(dso_id=dso_id))


@optional_cache
async def invalidate_clinic_team_members_cache(*, clinic_id: UUID) -> None:
    await async_redis.delete(clinic_team_members_cache_key(clinic_id=clinic_id))
