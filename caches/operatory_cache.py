import json
from uuid import UUID 
from core.queue import redis_client


OPERATORY_APPOINTMENTS_TTL_SECONDS = 30

def operatory_day_cache_key(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str
) -> str: 
    return(
        f"operatory_appts:"
        f"clinic:{clinic_id}"
        f"Op:{operatory}:"
        f"from:{date_start}:"
        f"to:{date_end}"
    )

def get_operatory_day_appointments_cached(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str
)->  list[dict] | None:
    
    key = operatory_day_cache_key(
        clinic_id=clinic_id,
        operatory= operatory,
        date_start=date_start,
        date_end=date_end
    )

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


def set_operatory_day_appointments_cached(
        *,
        clinic_id: UUID,
        operatory: int,
        date_start: str,
        date_end: str,
        appointments: list[dict],
) -> None:
    key = operatory_day_cache_key(
        clinic_id=clinic_id,
        operatory= operatory,
        date_start=date_start,
        date_end=date_end
)
    redis_client.setex(key, OPERATORY_APPOINTMENTS_TTL_SECONDS, json.dumps(appointments))

def invalidate_operatory_day_cache(
    *,
    clinic_id: UUID,
    operatory: int,
    date_start: str,
    date_end: str,
) -> None:
    key = operatory_day_cache_key(
        clinic_id=clinic_id,
        operatory=operatory,
        date_start=date_start,
        date_end=date_end,
    )
    redis_client.delete(key)






