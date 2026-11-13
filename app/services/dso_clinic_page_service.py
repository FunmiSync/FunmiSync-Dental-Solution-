from app.services.audit_logs import record_audit
from datetime import datetime, timedelta, time, timezone
from typing import Any, cast as type_cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from app.security.encryption import decode_secret
from app.services.errors import NotFound, OperationFailed
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import  SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.entities import AppointmentSyncLog, RegisteredClinics, SyncStatus, RoleAssignment, ScopeType, RoleType
from app.schemas.api import dso_clinic_actions_out, dso_clinic_disabled_out, dso_clinic_summary_Out, dso_clinic_list_out, dso_clinic_row_out
from app.infrastructure.cache.dso_clinic_page_cache import DSO_CLINIC_LIST_TTL_SECONDS, cache_get_json, cache_set_json, dso_clinic_list_cache_Key, invalidate_dso_clinic_list_cache
from app.security.rbac import get_dso_role
from app.services.clinic_health import as_utc, unreachable_too_long
from app.services.sync_alerts import open_clinic_alerts
import logging

logger = logging.getLogger(__name__)

WEBHOOK_FAILURE_THRESHOLD = 3
WEBHOOK_FAILURE_WINDOW = timedelta(hours=1)

def today_window()-> tuple[datetime, datetime]:
    today= datetime.now(timezone.utc).date()
    start = datetime.combine(today, time.min, tzinfo= timezone.utc)
    end = start + timedelta(days= 1)
    return start, end 

def to_clinic_timezone(value: datetime | None, clinic_timezone: str)-> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo = timezone.utc)

    try:
        return value.astimezone(ZoneInfo(clinic_timezone))
    except ZoneInfoNotFoundError:
        return value.astimezone(timezone.utc)
    

def clinic_status(clinic: RegisteredClinics) -> str:
    if clinic.is_disabled:
        return "disabled"
    return "active"

def is_missing_or_bad_secret(value: str | None)-> bool:
    if not value:
        return True
    
    try:
        decoded = decode_secret(value)
    except Exception:
        return True

    return not bool(decoded)

def has_invalid_clinic_config(clinic: RegisteredClinics) -> bool:
    if not clinic.crm_type:
        return True

    if not clinic.location_id:
        return True

    if not clinic.operatory_calendar_map:
        return True

    required_encrypted = [
        clinic.webhook_secret,
        clinic.od_developer_key,
        clinic.od_customer_key,
        clinic.crm_api_key,
        clinic.calendar_id,
    ]

    return any(is_missing_or_bad_secret(value) for value in required_encrypted)

def has_recent_webhook_auth_problem(clinic: RegisteredClinics, now: datetime) -> bool:
    if clinic.last_webhook_auth_failed_at is None:
        return False

    if clinic.webhook_auth_failure_count < WEBHOOK_FAILURE_THRESHOLD:
        return False

    return (now - clinic.last_webhook_auth_failed_at) <= WEBHOOK_FAILURE_WINDOW


def clinic_attention_reason(clinic: RegisteredClinics, now: datetime,
                            open_alerts: tuple[int, str] | None = None) -> str | None:
    """open_alerts: (number of open clinic sync alerts, the oldest one's message)."""
    if has_invalid_clinic_config(clinic):
        return "Clinic settings are incomplete"

    if clinic.od_health_status == "auth_failed":
        return clinic.od_health_reason or "OpenDental credentials failed"

    if unreachable_too_long(clinic, now):
        # The clinic's CRM changes are kept and sent once OpenDental answers again.
        return f"OpenDental has not answered since {as_utc(clinic.od_health_changed_at):%d %b %H:%M} UTC"

    if clinic.crm_health_status == "auth_failed":
        return clinic.crm_health_reason or "CRM credentials failed"

    if has_recent_webhook_auth_problem(clinic, now):
        return "Webhook header validation failed"

    if open_alerts:
        count, oldest = open_alerts
        return oldest if count == 1 else f"{count} problems are holding CRM changes back. Oldest: {oldest}"

    return None


def build_row_actions(
        *,
        dso_role: RoleAssignment | None,
        clinic_role: RoleAssignment | None,
)-> dso_clinic_actions_out:
    is_dso_admin_or_manager = (
        dso_role is not None and dso_role.role in {RoleType.ADMIN, RoleType.MANAGER} 
        )

    has_clinic_access = clinic_role is not None
    has_clinic_manage = (
        clinic_role is not None and clinic_role.role in {RoleType.ADMIN, RoleType.MANAGER}
    )


    return dso_clinic_actions_out(
        can_view= has_clinic_access,
        can_edit= has_clinic_manage,
        can_disable= is_dso_admin_or_manager
    )


async def build_dso_clinic_list(
    db: AsyncSession,
    *,
    dso_id:UUID,
    user_id:UUID,
    search: str | None,
    status_filter: str | None,
    limit:int,
    offset: int
) -> dso_clinic_list_out:
    cache_key = dso_clinic_list_cache_Key(
        dso_id=dso_id,
        user_id=user_id,
        search=search,
        status_filter= status_filter,
        limit=limit,
        offset=offset
    )

    cached = await cache_get_json(cache_key)
    if cached:
        logger.info(
            "DSO clinic page cache hit",
            extra={
                "dso_id": str(dso_id),
                "user_id": str(user_id),
                "has_search": bool(search),
                "status_filter": status_filter,
                "limit": limit,
                "offset": offset,
            },
        )
        return dso_clinic_list_out(**cached)
    
    logger.info(
        "Building DSO clinic page",
        extra={
            "dso_id": str(dso_id),
            "user_id": str(user_id),
            "has_search": bool(search),
            "status_filter": status_filter,
            "limit": limit,
            "offset": offset,
        },
    )

    start_dt, end_dt = today_window()

    query = select(RegisteredClinics).where(RegisteredClinics.dso_id == dso_id)

    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                RegisteredClinics.clinic_name.ilike(pattern),
                RegisteredClinics.clinic_timezone.ilike(pattern),
                cast(RegisteredClinics.clinic_number, String).ilike(pattern)
            )
        )

    clinics = (await db.scalars(query.order_by(RegisteredClinics.clinic_name.asc()))).all()
    clinic_ids= [clinic.id for clinic in clinics]

    dso_role = await get_dso_role(db, user_id, dso_id)

    clinic_roles = (
        (await db.scalars(select(RoleAssignment).where(
            RoleAssignment.user_id == user_id,
            RoleAssignment.scope_type == ScopeType.CLINIC,
            RoleAssignment.clinic_id.in_(clinic_ids),
            RoleAssignment.is_active.is_(True),
        ))).all()
        if clinic_ids
        else []
    )

    clinic_role_map: dict[UUID, RoleAssignment] = {}
    for row in clinic_roles:
        if row.clinic_id is not None:
            clinic_role_map[UUID(str(row.clinic_id))] = row

    rows = (
            (await db.execute(select(
                AppointmentSyncLog.clinic_id, func.count(AppointmentSyncLog.id)
                .filter(
                    AppointmentSyncLog.started_at >= start_dt,
                    AppointmentSyncLog.started_at < end_dt,
                    AppointmentSyncLog.sync_status == SyncStatus.PROCESSED
                )
                .label("synced_today"),
                func.max(AppointmentSyncLog.started_at).label("last_sync_at"))
                .where(
                    AppointmentSyncLog.clinic_id.in_(clinic_ids)).group_by(
                        AppointmentSyncLog.clinic_id
                    ))).all()
                    if clinic_ids
                    else[]
                )

        
    stats_by_clinic: dict[UUID, object] = {}
    for row in rows:
        if row.clinic_id is not None:
            stats_by_clinic[UUID(str(row.clinic_id))] = row

    alerts_by_clinic = await open_clinic_alerts(db, clinic_ids)
    all_items: list[dso_clinic_row_out] = []
    now = datetime.now(timezone.utc)

    for clinic in clinics:
        stats = type_cast(Any, stats_by_clinic.get(clinic.id))
        clinic_role = clinic_role_map.get(clinic.id)
        status_value = clinic_status(clinic)

        if status_filter and status_value != status_filter:
            continue
        
        synced_today = int(stats.synced_today or 0) if stats else 0
        last_sync_at = stats.last_sync_at if stats else None 
        attention_reason = clinic_attention_reason(clinic, now, alerts_by_clinic.get(clinic.id))
        needs_attention = attention_reason is not None

        all_items.append(
            dso_clinic_row_out(
                id= clinic.id,
                clinic_name= clinic.clinic_name,
                clinic_number= clinic.clinic_number,
                clinic_timezone= clinic.clinic_timezone,
                synced_today=synced_today,
                last_sync_at= last_sync_at,
                status= status_value,
                needs_attention= needs_attention,
                attention_reason= attention_reason,
                disabled_at= clinic.disabled_at,
                actions = build_row_actions(
                    dso_role=dso_role,
                    clinic_role=clinic_role
                )
            )
        )

    page_items= all_items[offset: offset + limit]

    response = dso_clinic_list_out(
        generated_at= datetime.now(timezone.utc),
        visible_count= len(page_items),
        summary= dso_clinic_summary_Out(
            total_clinics=len(clinics),
            active_clinics= sum(1 for item in all_items if item.status == "active"),
            disabled_clinics= sum(1 for item in all_items if item.status == "disabled"),
            needs_attention = sum(1 for item in all_items if item.needs_attention),
            synced_today= sum(item.synced_today for item in all_items)
        ),
        items= page_items,
    )

    await cache_set_json(
        cache_key,
        response.model_dump(mode= "json"),
        DSO_CLINIC_LIST_TTL_SECONDS
    )

    logger.info(
        "Built DSO clinic page",
        extra={
            "dso_id": str(dso_id),
            "user_id": str(user_id),
            "matched_clinics": len(clinics),
            "returned_items": len(page_items),
            "active_clinics": response.summary.active_clinics,
            "disabled_clinics": response.summary.disabled_clinics,
            "needs_attention": response.summary.needs_attention,
            "synced_today": response.summary.synced_today,
        },
    )

    return response

    

######## disable CLINIC 
async def disable_dso_clinic(
    db: AsyncSession,
    *,
    dso_id: UUID,
    clinic_id: UUID,
    disabled_by:UUID
)-> dso_clinic_disabled_out:

    clinic = await db.scalar(select(RegisteredClinics).where(RegisteredClinics.id == clinic_id, RegisteredClinics.dso_id == dso_id).limit(1))

    if clinic is None:
        logger.warning(
            "Clinic not found while disabling DSO clinic",
            extra={
                "dso_id": str(dso_id),
                "clinic_id": str(clinic_id),
                "disabled_by": str(disabled_by),
            },
        )
        raise NotFound("Clinic not found in this DSO")
    now = datetime.now(timezone.utc)

    if not clinic.is_disabled:
        clinic.is_disabled = True
        clinic.disabled_at = now 
        clinic.disabled_by = disabled_by

        try:
            record_audit(db,clinic_id=clinic.id,entity_type="clinic",entity_id=clinic.id,action="disable",details={"actor_user_id":str(disabled_by)})
            await db.commit()
            await db.refresh(clinic)
            await invalidate_dso_clinic_list_cache(dso_id=dso_id)
            logger.info(
                "Clinic disabled successfully",
                extra={
                    "dso_id": str(dso_id),
                    "clinic_id": str(clinic.id),
                    "clinic_name": clinic.clinic_name,
                    "disabled_by": str(disabled_by),
                },
            )
        except SQLAlchemyError:
            await db.rollback()
            logger.exception(
                "Failed to disable clinic",
                extra={
                    "dso_id": str(dso_id),
                    "clinic_id": str(clinic_id),
                    "disabled_by": str(disabled_by),
                },
            )
            raise OperationFailed("Unable to disable clinic at this time")
        

    else:
        logger.info(
            "Disable requested for already-disabled clinic",
            extra={
                "dso_id": str(dso_id),
                "clinic_id": str(clinic.id),
                "clinic_name": clinic.clinic_name,
                "disabled_by": str(disabled_by),
            },
        )

    await invalidate_dso_clinic_list_cache(dso_id=dso_id)

    logger.info(
        "Invalidated DSO clinic list cache after disable",
        extra={
            "dso_id": str(dso_id),
            "clinic_id": str(clinic.id),
        },
    )

    return dso_clinic_disabled_out(
        id=clinic.id,
        clinic_name=clinic.clinic_name,
        status="disabled",
        disabled_at=clinic.disabled_at or now,
    )
