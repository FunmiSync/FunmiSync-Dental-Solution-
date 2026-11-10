from datetime import datetime, timezone
from app.models.entities import RegisteredClinics



def now()-> datetime:
    return datetime.now(timezone.utc)

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

def mark_crm_auth_failed(clinic: RegisteredClinics, *, reason: str)-> None:
    clinic.crm_health_status = "auth_failed"
    clinic.crm_health_reason = reason
    clinic.crm_health_changed_at = now()

def mark_crm_health_ok(clinic: RegisteredClinics) -> None:
    clinic.crm_health_status = "ok"
    clinic.crm_health_reason = None
    clinic.crm_health_changed_at = now()