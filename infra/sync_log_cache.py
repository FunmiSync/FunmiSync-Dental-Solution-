import json 
from datetime import date, datetime, timezone
from uuid import UUID
from core.queue import redis_client



def resolve_dates(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    today = datetime.now(timezone.utc).date()

    if date_from is None and date_to is None:
        return today, today
    if date_from is None and date_to is not None:
        return date_to, date_to 
    if date_from is not None  and date_to is None:
        return date_from , date_from
    
    return date_from, date_to #type: ignore[return-value]


def date_str(value: date) -> str:
    return value.isoformat()

def value_str(value: object | None) -> str:
    return str(value) if value is not None else "none"

def summary_ttl_seconds(*, date_from: date | None, date_to: date | None) -> int:
    _, resolved_to = resolve_dates(date_from, date_to)
    today = datetime.now(timezone.utc).date()

    if resolved_to == today:
        return 180
    
    days_old = (today - resolved_to).days
    if days_old in (1,2):
        return 43200
    
    return 86400


def page_ttl_seconds(*, date_from: date | None, date_to: date | None, cursor: str | None) -> int:
    _, resolved_to = resolve_dates(date_from, date_to)
    today = datetime.now(timezone.utc).date()

    if resolved_to == today:
        return 180 if cursor is None else 300

    days_old = (today - resolved_to).days
    if days_old in (1, 2):
        return 43200

    return 86400



def summary_cache_key(*, scope: str, scope_id: UUID, clinic_filter_id: UUID | None, date_from: date | None, date_to: date | None)-> str:
    resolved_from, resolved_to = resolve_dates(date_from, date_to)
    return (
        f"sync_logs:summary:{scope}:{scope_id}:"
        f"clinic:{value_str(clinic_filter_id)}:"
        f"from:{date_str(resolved_from)}:"
        f"to:{date_str(resolved_to)}"
    )

def page_cache_key(
        *,
        scope:str,
        scope_id: UUID,
        clinic_filter_id: UUID | None,
        status: str | None,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None,
        limit: int
) -> str:
    resolved_from, resolved_to = resolve_dates(date_from, date_to)
    return (
        f"sync_logs: page:{scope}:{scope_id}:"
        f"clinic: {value_str(clinic_filter_id)}:"
        f"status: {value_str(status)}:"
        f"from:{date_str(resolved_from)}:"
        f"to:{date_str(resolved_to)}:"
        f"cursor:{cursor or 'first'}:"
        f"limit:{limit}"
    )

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

def cache_set_json(key: str, payload:dict, ttl_seconds:int)-> None:
    redis_client.setex(key, ttl_seconds, json.dumps(payload))


def invalidate_hot_sync_log_cache(*, dso_id:UUID | None, clinic_id: UUID)-> None:
    today = datetime.now(timezone.utc).date().isoformat()

    patterns =[
        f"sync_logs:summary:clinic:{clinic_id}:*to:{today}",
        f"sync_logs:page:clinic:{clinic_id}:*to:{today}:cursor:first:*",
    ]

    if dso_id is not None:
        patterns.extend(
            [
                f"sync_logs:summary:dso:{dso_id}:*to:{today}",
                f"sync_logs:page:dso:{dso_id}:*to:{today}:cursor:first:*"
            ]
        )
    
    for pattern in patterns:
        for key in redis_client.scan_iter(match=pattern):
            redis_client.delete(key)
