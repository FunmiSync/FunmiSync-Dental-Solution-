from datetime import datetime, timedelta, timezone
from app.models.entities import RegisteredClinics

OPENDENTAL_UNREACHABLE = "unreachable"
# A short outage is not worth anyone's attention; after this long the clinic list shows it.
UNREACHABLE_ATTENTION_AFTER = timedelta(hours=1)


def now()-> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    # SQLite (tests) gives back naive datetimes; PostgreSQL gives aware ones.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def mark_webhook_auth_failed(clinic: RegisteredClinics)-> None:
    clinic.last_webhook_auth_failed_at = now()
    clinic.webhook_auth_failure_count = int(clinic.webhook_auth_failure_count or 0) + 1

def reset_webhook_failure_after_success(clinic:RegisteredClinics):
    clinic.webhook_auth_failure_count = 0
    clinic.last_webhook_auth_failed_at = None

def mark_od_auth_failed(clinic: RegisteredClinics, *, reason: str)-> None:
    clinic.od_health_status ="auth_failed"
    clinic.od_health_reason = reason
    clinic.od_health_changed_at = now()

def mark_od_health_ok(clinic:RegisteredClinics)-> None:
    clinic.od_health_status = "ok"
    clinic.od_health_reason = None
    clinic.od_health_changed_at = now()
    clinic.sync_paused_until = None  # OpenDental answered: the clinic's line can move again


def mark_od_unreachable(clinic: RegisteredClinics, *, reason: str, at: datetime | None = None) -> datetime:
    """Pause the clinic's line of CRM events; returns when OpenDental is tried again.

    od_health_changed_at keeps the time OpenDental first stopped answering, so the wait
    grows with the outage: every 2 minutes at first, then 5, then 15.
    """
    at = at or now()
    if clinic.od_health_status != OPENDENTAL_UNREACHABLE or clinic.od_health_changed_at is None:
        clinic.od_health_status = OPENDENTAL_UNREACHABLE
        clinic.od_health_changed_at = at
    clinic.od_health_reason = reason
    down_for = at - as_utc(clinic.od_health_changed_at)
    if down_for < timedelta(minutes=10):
        wait = timedelta(minutes=2)
    elif down_for < timedelta(hours=1):
        wait = timedelta(minutes=5)
    else:
        wait = timedelta(minutes=15)
    clinic.sync_paused_until = at + wait
    return clinic.sync_paused_until


def opendental_paused_until(clinic: RegisteredClinics, at: datetime) -> datetime | None:
    """When the clinic's paused line tries OpenDental again, or None if it is not paused."""
    if clinic.sync_paused_until is None or as_utc(clinic.sync_paused_until) <= at:
        return None
    return as_utc(clinic.sync_paused_until)


def unreachable_too_long(clinic: RegisteredClinics, at: datetime) -> bool:
    return (clinic.od_health_status == OPENDENTAL_UNREACHABLE and clinic.od_health_changed_at is not None
            and at - as_utc(clinic.od_health_changed_at) >= UNREACHABLE_ATTENTION_AFTER)

def mark_crm_auth_failed(clinic: RegisteredClinics, *, reason: str)-> None:
    clinic.crm_health_status = "auth_failed"
    clinic.crm_health_reason = reason
    clinic.crm_health_changed_at = now()

def mark_crm_health_ok(clinic: RegisteredClinics) -> None:
    clinic.crm_health_status = "ok"
    clinic.crm_health_reason = None
    clinic.crm_health_changed_at = now()
