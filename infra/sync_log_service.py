import base64
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, TypeAlias, cast
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from auth.security import decode_secret, decode_json_secret
from core.models import AppointmentSyncLog, RegisteredClinics, SyncStatus
from core.schemas import (
    sync_log_clinic_option_out,
    sync_log_page_out,
    sync_log_row_out,
    sync_log_summary_out,
    sync_log_detail_out
)
from infra.sync_log_cache import (
    cache_get_json,
    cache_set_json,
    page_cache_key,
    page_ttl_seconds,
    summary_cache_key,
    summary_ttl_seconds,
)


SyncDirectionValue: TypeAlias = Literal["crm_to_od", "od_to_crm"]
SyncStatusValue: TypeAlias = Literal[
    "queued",
    "processing",
    "retrying",
    "processed",
    "failed",
]
SyncLogQueryRow: TypeAlias = tuple[AppointmentSyncLog, RegisteredClinics]


def _direction_value(value: object) -> SyncDirectionValue:
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value).lower()

    if normalized == "crm_to_od":
        return "crm_to_od"
    if normalized == "od_to_crm":
        return "od_to_crm"

    raise ValueError(f"Unsupported sync direction: {value}")


def _status_value(value: object) -> SyncStatusValue:
    raw_value = getattr(value, "value", value)
    normalized = str(raw_value).lower()

    if normalized == "queued":
        return "queued"
    if normalized == "processing":
        return "processing"
    if normalized == "retrying":
        return "retrying"
    if normalized == "processed":
        return "processed"
    if normalized == "failed":
        return "failed"

    raise ValueError(f"Unsupported sync status: {value}")


def direction_label(direction: SyncDirectionValue) -> str:
    mapping = {
        "crm_to_od": "CRM -> OpenDental",
        "od_to_crm": "OpenDental -> CRM",
    }
    return mapping[direction]


def status_label(status: SyncStatusValue) -> str:
    mapping = {
        "queued": "In Progress",
        "processing": "In Progress",
        "retrying": "Needs Retry",
        "processed": "Synced",
        "failed": "Failed",
    }
    return mapping[status]


def record_label(log: AppointmentSyncLog) -> str:
    if log.apt_num is not None:
        return f"Appointment - Apt_{log.apt_num}"
    if log.event_id:
        return f"Appointment - {log.event_id}"
    return "Appointment"


def what_happened(log: AppointmentSyncLog) -> str:
    operation = (log.operation or "").strip().lower()
    appointment_status = (log.appointment_status or "").strip().lower()

    if appointment_status in {"cancelled", "canceled"}:
        return "Appointment Cancelled"
    if operation == "create":
        return "Appointment Created"
    if operation == "update":
        return "Appointment Updated"
    if operation == "unchanged":
        return "Appointment Unchanged"

    return "Appointment Sync"


def _encode_cursor(started_at: datetime, row_id: UUID) -> str:
    payload = {
        "started_at": started_at.isoformat(),
        "id": str(row_id),
    }
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8"))
        payload = json.loads(raw.decode("utf-8"))
        started_at = datetime.fromisoformat(payload["started_at"])
        row_id = UUID(payload["id"])
        return started_at, row_id
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid cursor") from exc


def _resolve_date_window(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime, datetime]:
    today = datetime.now(timezone.utc).date()

    if date_from is None and date_to is None:
        date_from = today
        date_to = today

    if date_from is None:
        date_from = date_to

    if date_to is None:
        date_to = date_from

    if date_from is None or date_to is None:
        raise HTTPException(status_code=400, detail="Invalid date window")

    if date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")

    start_dt = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(
        date_to + timedelta(days=1),
        time.min,
        tzinfo=timezone.utc,
    )
    return start_dt, end_dt


def base_scope_query(db: Session, dso_id: UUID):
    return (
        db.query(AppointmentSyncLog, RegisteredClinics)
        .join(RegisteredClinics, AppointmentSyncLog.clinic_id == RegisteredClinics.id)
        .filter(RegisteredClinics.dso_id == dso_id)
    )

def clinic_scope_query(db:Session, clinic_id: UUID):
    return db.query(AppointmentSyncLog, RegisteredClinics).join(RegisteredClinics, AppointmentSyncLog.clinic_id == RegisteredClinics.id).filter(RegisteredClinics.id == clinic_id)


def serialize_key(log: AppointmentSyncLog, clinic: RegisteredClinics) -> sync_log_row_out:
    raw_direction = _direction_value(log.direction)
    raw_status = _status_value(log.sync_status)

    return sync_log_row_out(
        id=log.id,
        started_at=log.started_at,
        clinic_id=clinic.id,
        clinic_name=clinic.clinic_name,
        patient_name=decode_secret(log.patient_name),
        record_label=record_label(log),
        what_happened=what_happened(log),
        direction=raw_direction,
        direction_label=direction_label(raw_direction),
        status=raw_status,
        status_label=status_label(raw_status),
        reason=log.reason,
        event_id=log.event_id,
        apt_num=log.apt_num,
        operation=log.operation,
        attempt_count=log.attempt_count,
    )

#DSO LEVEL 
def build_dso_summary(
    db: Session,
    dso_id: UUID,
    clinic_id: UUID | None,
    date_from: date | None,
    date_to: date | None,
) -> sync_log_summary_out:
    start_dt, end_dt = _resolve_date_window(date_from, date_to)

    query = base_scope_query(db, dso_id).filter(
        AppointmentSyncLog.started_at >= start_dt,
        AppointmentSyncLog.started_at < end_dt,
    )

    if clinic_id is not None:
        query = query.filter(AppointmentSyncLog.clinic_id == clinic_id)

    synced_today = query.filter(
        AppointmentSyncLog.sync_status == SyncStatus.PROCESSED
    ).count()
    in_progress = query.filter(
        AppointmentSyncLog.sync_status.in_([SyncStatus.QUEUED, SyncStatus.PROCESSING])
    ).count()
    needs_attention = query.filter(
        AppointmentSyncLog.sync_status == SyncStatus.RETRYING
    ).count()
    failed = query.filter(
        AppointmentSyncLog.sync_status == SyncStatus.FAILED
    ).count()

    return sync_log_summary_out(
        synced_today=synced_today,
        in_progress=in_progress,
        needs_attention=needs_attention,
        failed=failed,
    )


#DSO Summary cache
def build_summary_cached(
        db: Session,
        *,
        dso_id: UUID,
        clinic_id:UUID,
        date_from: date | None,
        date_to: date | None
)-> sync_log_summary_out:
    key = summary_cache_key(
        scope="dso",
        scope_id=dso_id,
        clinic_filter_id=clinic_id,
        date_from=date_from,
        date_to=date_to
    )

    cached = cache_get_json(key)
    if cached:
        return sync_log_summary_out(**cached)
    
    summary = build_dso_summary(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        date_from=date_from,
        date_to=date_to
    )

    cache_set_json(
        key,
        summary.model_dump(mode="json"),
        summary_ttl_seconds(date_from=date_from, date_to=date_to)
    )
    
    return summary



# CLINIC LEVEL 
def build_clinic_level_summary(db: Session, clinic_id: UUID, date_from: date | None, date_to: date | None) -> sync_log_summary_out:

    start_dt, end_dt = _resolve_date_window(date_from , date_to)

    query = clinic_scope_query(db, clinic_id).filter(AppointmentSyncLog.started_at >= start_dt, AppointmentSyncLog.started_at < end_dt)

    synced_today = query.filter(AppointmentSyncLog.sync_status == SyncStatus.PROCESSED).count()

    in_progress = query.filter(AppointmentSyncLog.sync_status.in_([SyncStatus.QUEUED, SyncStatus.PROCESSING])).count()

    needs_attention = query.filter(AppointmentSyncLog.sync_status == SyncStatus.RETRYING).count()

    failed = query.filter(
        AppointmentSyncLog.sync_status == SyncStatus.FAILED
    ).count()

    return sync_log_summary_out(
        synced_today=synced_today,
        in_progress=in_progress,
        needs_attention=needs_attention,
        failed=failed,
    )


#clinic summary cache 
def build_clinic_level_summary_cached(
        db:Session,
        *,
        clinic_id: UUID,
        date_from: date | None,
        date_to: date | None
) -> sync_log_summary_out:
    
    key= summary_cache_key(
        scope= "clinic",
        scope_id= clinic_id,
        clinic_filter_id=None,
        date_from=date_from,
        date_to=date_to
    ) 
    cached = cache_get_json(key)
    if cached:
        return sync_log_summary_out(**cached)
    
    summary = build_clinic_level_summary(
        db,
        clinic_id=clinic_id,
        date_from=date_from,
        date_to=date_to
    )

    cache_set_json(
        key,
        summary.model_dump(mode="json"),
        summary_ttl_seconds(date_from=date_from, date_to=date_to)
    )

    return summary


#DSO LEVEL 
def build_clinic_options(
    db: Session,
    dso_id: UUID,
) -> list[sync_log_clinic_option_out]:
    clinics = (
        db.query(RegisteredClinics)
        .filter(RegisteredClinics.dso_id == dso_id)
        .order_by(RegisteredClinics.clinic_name.asc())
        .all()
    )

    return [
        sync_log_clinic_option_out(id=clinic.id, name=clinic.clinic_name)
        for clinic in clinics
    ]


#clininc level 
def build_single_clinic_option(db: Session, clinic_id: UUID) -> list[sync_log_clinic_option_out]:
    clinic = db.query(RegisteredClinics).filter(RegisteredClinics.id == clinic_id).first()

    if clinic is None :
        raise HTTPException(status_code= status.HTTP_404_NOT_FOUND, detail= "Clinic not Found")

    return [
        sync_log_clinic_option_out(
            id = clinic.id,
            name= clinic.clinic_name
        )
    ]


###DSO LEVEL
def build_items(
    db: Session,
    *,
    dso_id: UUID,
    clinic_id: UUID | None,
    status: SyncStatus | None,
    limit: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
) -> tuple[list[sync_log_row_out], str | None]:
    start_dt, end_dt = _resolve_date_window(date_from, date_to)

    query = base_scope_query(db, dso_id).filter(
        AppointmentSyncLog.started_at >= start_dt,
        AppointmentSyncLog.started_at < end_dt,
    )

    if clinic_id is not None:
        query = query.filter(AppointmentSyncLog.clinic_id == clinic_id)

    if status is not None:
        query = query.filter(AppointmentSyncLog.sync_status == status)

    if cursor:
        cursor_started_at, cursor_id = _decode_cursor(cursor)
        query = query.filter(
            or_(
                AppointmentSyncLog.started_at < cursor_started_at,
                and_(
                    AppointmentSyncLog.started_at == cursor_started_at,
                    AppointmentSyncLog.id < cursor_id,
                ),
            )
        )

    rows = cast(
        list[SyncLogQueryRow],
        query.order_by(
            AppointmentSyncLog.started_at.desc(),
            AppointmentSyncLog.id.desc(),
        )
        .limit(limit + 1)
        .all(),
    )

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [serialize_key(log, clinic) for log, clinic in rows]

    next_cursor: str | None = None
    if has_more and rows:
        last_log, _last_clinic = rows[-1]
        next_cursor = _encode_cursor(last_log.started_at, last_log.id)

    return items, next_cursor


# dso items cached
def build_dso_items_cached(
        db: Session,
        *,
        dso_id: UUID,
        clinic_id: UUID | None,
        status: SyncStatus | None,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None
) -> tuple[list[sync_log_row_out], str | None]:
    key = page_cache_key(
        scope= "dso",
        scope_id= dso_id,
        clinic_filter_id=clinic_id,
        status= status.value if status else None,
        date_from=date_from,
        date_to=date_to,
        cursor= cursor,
        limit=limit
        )
    
    cached= cache_get_json(key)
    if cached:
        items= [sync_log_row_out(**item) for item in cached["items"]]
        return items, cached["next_cursor"]
    
    items, next_cursor = build_items(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        status=status,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor
    )

    cache_set_json(
        key,
        {
            "items": [item.model_dump(mode="json") for item in items],
            "next_cursor": next_cursor
        },
         page_ttl_seconds(date_from=date_from, date_to=date_to, cursor=cursor),
    )

    return items, next_cursor



##CLINIC LEVEL 
def build_clinic_items(
        db: Session,
        *,
        clinic_id: UUID,
        status: SyncStatus | None,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None
) -> tuple[list[sync_log_row_out], str | None]:

    
    start_dt, end_dt = _resolve_date_window(date_from, date_to)

    query = clinic_scope_query(db, clinic_id).filter(AppointmentSyncLog.started_at >= start_dt,AppointmentSyncLog.started_at < end_dt )

    if status is not None:
        query = query.filter(AppointmentSyncLog.sync_status == status)
    
    if cursor:
        cursor_started_at, cursor_id = _decode_cursor(cursor)
        query = query.filter(or_(AppointmentSyncLog.started_at < cursor_started_at ), and_( AppointmentSyncLog.started_at == cursor_started_at, AppointmentSyncLog.id < cursor_id))


    rows =cast(list[SyncLogQueryRow], query.order_by(AppointmentSyncLog.started_at.desc(), AppointmentSyncLog.id.desc()).limit(limit + 1).all() )


    has_more =len(rows) > limit

    if has_more:
        rows = rows[:limit]

    items = [serialize_key(log, clinic) for log, clinic in rows]

    next_cursor: str | None = None
    if has_more and rows:
        last_log, last_clinic = rows[-1]
        next_cursor = _encode_cursor(last_log.started_at, last_log.id)
    
    return items, next_cursor 



def build_clinic_items_cached(
        db:Session,
        *,
        clinic_id:UUID,
        status: SyncStatus | None,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None
        )-> tuple[list[sync_log_row_out], str | None]:
        
        key = page_cache_key(
            scope="clinic",
            scope_id= clinic_id,
            clinic_filter_id= None,
            status= status.value if status else None,
            date_from= date_from,
            date_to=date_to,
            cursor=cursor,
            limit=limit
    )
        
        cached = cache_get_json(key)
        if cached:
            items = [sync_log_row_out(**item) for item in cached["items"]]
            return items, cached["next_cursor"]
        
        items,  next_cursor = build_clinic_items(
            db,
            clinic_id=clinic_id,
            status=status,
            limit=limit,
            date_from=date_from,
            date_to=date_to,
            cursor=cursor
        )

        cache_set_json(
            key,
            {
                "items": [item.model_dump(mode="json") for item in items],
                "next_cursor": next_cursor
            },
            page_ttl_seconds(date_from=date_from, date_to=date_to, cursor=cursor)
        )
        return items, next_cursor
        

    
###DSO LEVEL
def build_page_snapshot(
    db: Session,
    *,
    dso_id: UUID,
    clinic_id: UUID | None,
    status: SyncStatus | None,
    limit: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
) -> sync_log_page_out:
    items, next_cursor = build_items(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        status=status,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )

    return sync_log_page_out(
        generated_at=datetime.now(timezone.utc),
        visible_count=len(items),
        summary=build_dso_summary(
            db,
            dso_id=dso_id,
            clinic_id=clinic_id,
            date_from=date_from,
            date_to=date_to,
        ),
        clinics=build_clinic_options(db, dso_id),
        items=items,
        next_cursor=next_cursor,
    )



def build_dso_page_snapshot_cached(
        db:Session,
        *,
        dso_id:UUID,
        clinic_id: UUID| None,
        status: SyncStatus | None,
        limit:int,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None
) -> sync_log_page_out:
    
    items, next_cursor = build_dso_items_cached(
        db,
        dso_id=dso_id,
        clinic_id=clinic_id,
        status=status,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor
    )

    return sync_log_page_out(
        generated_at=datetime.now(timezone.utc),
        visible_count=len(items),
        summary=build_summary_cached(
            db,
            dso_id=dso_id,
            clinic_id= clinic_id,
            date_from=date_from,
            date_to=date_to,
        ),
        clinics= build_clinic_options(db, dso_id),
        items= items,
        next_cursor= next_cursor
    )


    #####CLINIC LEVEL 
def build_clinic_page_snapshot(
        db: Session,
        *,
        clinic_id: UUID,
        status: SyncStatus | None,
        limit: int,
        date_from: date | None,
        date_to: date | None,
        cursor: str | None

) -> sync_log_page_out:
    
    items, next_cursor = build_clinic_items(
        db, clinic_id = clinic_id, status=status, limit=limit, date_from = date_from, date_to = date_to, cursor=cursor 
    )
    
    return sync_log_page_out(
        generated_at = datetime.now(timezone.utc),
        visible_count= len(items),
        summary= build_clinic_level_summary(
            db, clinic_id = clinic_id, date_from = date_from, date_to= date_to
        ),
        clinics = build_single_clinic_option(db, clinic_id),
        items= items,
        next_cursor=next_cursor

    )



def build_clinic_page_snapshot_cached(
    db: Session,
    *,
    clinic_id: UUID,
    status: SyncStatus | None,
    limit: int,
    date_from: date | None,
    date_to: date | None,
    cursor: str | None,
) -> sync_log_page_out:
    items, next_cursor = build_clinic_items_cached(
        db,
        clinic_id=clinic_id,
        status=status,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )

    return sync_log_page_out(
        generated_at=datetime.now(timezone.utc),
        visible_count=len(items),
        summary=build_clinic_level_summary_cached(
            db,
            clinic_id=clinic_id,
            date_from=date_from,
            date_to=date_to,
        ),
        clinics=build_single_clinic_option(db, clinic_id),
        items=items,
        next_cursor=next_cursor,
    )



####dso level 
def build_sync_log_detail(
        db:Session,
        *,
        dso_id: UUID,
        sync_log_id: UUID
) -> sync_log_detail_out:
    
    row =cast(SyncLogQueryRow | None, base_scope_query(db, dso_id).filter(AppointmentSyncLog.id == sync_log_id).first())

    if row is None:
        raise HTTPException(status_code = status.HTTP_404_NOT_FOUND, detail="Sync log not found")
    
    log, clinic = row 
    base = serialize_key(log, clinic)

    return sync_log_detail_out(
        **base.model_dump(),
        completed_at = log.completed_at,
        appointment_id = log.appointment_id,
        inbound_event_id= log.inbound_event_id,
        pat_id = log.pat_id,
        appointment_status = log.appointment_status,
        payload = decode_json_secret(log.payload)
    )


def build_clinic_sync_log_detail(
    db: Session,
    *,
    clinic_id: UUID,
    sync_log_id: UUID,
) -> sync_log_detail_out:
    row = cast(
        SyncLogQueryRow | None,
        clinic_scope_query(db, clinic_id)
        .filter(AppointmentSyncLog.id == sync_log_id)
        .first(),
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Sync log not found")

    log, clinic = row
    base = serialize_key(log, clinic)

    return sync_log_detail_out(
        **base.model_dump(),
        completed_at=log.completed_at,
        appointment_id=log.appointment_id,
        inbound_event_id=log.inbound_event_id,
        pat_id=log.pat_id,
        appointment_status=log.appointment_status,
        payload=decode_json_secret(log.payload),
    )

    

