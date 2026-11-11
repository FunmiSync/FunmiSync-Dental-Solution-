from uuid import UUID

from app.infrastructure.cache.resilience import decode_cached_text, encode_cache_payload, optional_cache
from app.infrastructure.queue import async_redis
from app.security.encryption import hash_lookup

DSO_CLINIC_LIST_TTL_SECONDS = 300


def value_part(value: object | None) -> str:
    return str(value) if value is not None else "none"


def dso_clinic_list_cache_Key(
        *,
        dso_id: UUID,
        user_id: UUID,
        search: str | None,
        status_filter: str | None,
        limit: int,
        offset: int
) -> str:
    return (
        f"dso_clinics:list:dso:{dso_id}:"
        f"user:{user_id}:"
        f"search:{hash_lookup(search) if search else 'none'}:"
        f"status:{value_part(status_filter)}:"
        f"limit:{limit}:"
        f"offset:{offset}"
    )


@optional_cache
async def cache_get_json(key: str) -> dict | None:
    return decode_cached_text(await async_redis.get(key))


@optional_cache
async def cache_set_json(key: str, payload: dict, ttl_seconds: int):
    await async_redis.setex(key, ttl_seconds, encode_cache_payload(payload))


@optional_cache
async def invalidate_dso_clinic_list_cache(*, dso_id: UUID) -> None:
    async for key in async_redis.scan_iter(match=f"dso_clinics:list:dso:{dso_id}:*"):
        await async_redis.delete(key)
