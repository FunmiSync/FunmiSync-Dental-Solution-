from uuid import UUID

from app.infrastructure.cache.resilience import decode_cached_text, encode_cache_payload, optional_cache
from app.infrastructure.queue import async_redis

OPERATORY_APPOINTMENTS_TTL_SECONDS = 30


def operatory_day_cache_key(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str
) -> str:
    return (
        f"operatory_appts:"
        f"clinic:{clinic_id}"
        f"Op:{operatory}:"
        f"from:{date_start}:"
        f"to:{date_end}"
    )


@optional_cache
async def get_operatory_day_appointments_cached(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str
) -> list[dict] | None:
    key = operatory_day_cache_key(clinic_id=clinic_id, operatory=operatory, date_start=date_start, date_end=date_end)
    return decode_cached_text(await async_redis.get(key))


@optional_cache
async def set_operatory_day_appointments_cached(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str,
        appointments: list[dict],
) -> None:
    key = operatory_day_cache_key(clinic_id=clinic_id, operatory=operatory, date_start=date_start, date_end=date_end)
    await async_redis.setex(key, OPERATORY_APPOINTMENTS_TTL_SECONDS, encode_cache_payload(appointments))


@optional_cache
async def invalidate_operatory_day_cache(
    *,
    clinic_id: UUID,
    operatory: int,
    date_start: str,
    date_end: str,
) -> None:
    key = operatory_day_cache_key(clinic_id=clinic_id, operatory=operatory, date_start=date_start, date_end=date_end)
    await async_redis.delete(key)
