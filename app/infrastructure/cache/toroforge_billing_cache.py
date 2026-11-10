from uuid import UUID

from app.infrastructure.cache.resilience import decode_cached_text, encode_cache_payload, optional_cache
from app.infrastructure.queue import async_redis

DSO_BILLING_TTL_SECONDS = 300
CLINIC__BILLING_TTL_SECONDS = 300


def dso_billing_cache_key(*, dso_id: UUID) -> str:
    return f"billing:command-center:dso:{dso_id}"


def clinic_billing_cache_key(*, clinic_id: UUID) -> str:
    return f"billing:command-center:clinic:{clinic_id}"


@optional_cache
async def cache_get_json(key: str) -> dict | None:
    return decode_cached_text(await async_redis.get(key))


@optional_cache
async def cache_set_json(key: str, payload: dict, ttl_seconds: int) -> None:
    await async_redis.setex(key, ttl_seconds, encode_cache_payload(payload))


@optional_cache
async def invalidate_dso_billing_cache(*, dso_id: UUID) -> None:
    await async_redis.delete(dso_billing_cache_key(dso_id=dso_id))


@optional_cache
async def invalidate_clinic_billing_cache(*, clinic_id: UUID, dso_id: UUID | None = None) -> None:
    await async_redis.delete(clinic_billing_cache_key(clinic_id=clinic_id))

    if dso_id is not None:
        await invalidate_dso_billing_cache(dso_id=dso_id)


async def invalidate_billing_scope(*, clinic_id: UUID | None, dso_id: UUID | None) -> None:
    # Run independently so a failed clinic invalidation still attempts the DSO.
    if clinic_id is not None:
        await invalidate_clinic_billing_cache(clinic_id=clinic_id)
    if dso_id is not None:
        await invalidate_dso_billing_cache(dso_id=dso_id)
