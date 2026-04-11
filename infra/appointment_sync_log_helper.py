from datetime import datetime, timezone
from sqlalchemy.orm import Session
from dataclasses import dataclass
from typing import Any, Optional
from auth.security import encrypt_json_secret, encrypt_secret, hash_lookup
from core.models import AppointmentSyncLog, SyncDirection,SyncStatus
from infra.sync_log_events import publish_sync_log_changed
import uuid

@dataclass
class SyncLogInput:
    clinic_id : uuid.UUID
    inbound_event_id: Optional[uuid.UUID]
    pat_id: Optional[uuid.UUID]
    appointment_id: Optional[uuid.UUID]
    contact_id : str
    event_id: Optional[str]
    apt_num: Optional[int]
    patient_name: Optional[str]
    date_str: str
    start_str: str
    end_str: str
    appointment_status: str
    direction: SyncDirection
    payload: Optional[dict[str, Any]]


class AppointmentSyncLogService:
    def __init__(self, db: Session):
        self.db = db

    def build_change_key(self, data:SyncLogInput)-> str:
        raw_key = (
                f"{data.clinic_id}|{data.contact_id}|{data.date_str}|"
                 f"{data.start_str}|{data.end_str}|"
        f"{data.appointment_status}|{data.direction.value}" 
        )
        return hash_lookup(raw_key)

    def publish_change(self, sync_log: AppointmentSyncLog) -> None:
        clinic_id = sync_log.clinic_id
        if clinic_id is None:
            return

        publish_sync_log_changed(
            self.db,
            clinic_id=clinic_id,
            sync_log_id=sync_log.id,
        )
    
    def normalize_search_text(self, value: str | None) -> str | None:
        if not value:
            return None
        
        normalized= " ".join(value.lower().strip().split())
        return normalized or None 
    
    def get_or_create_sync_log(self, data: SyncLogInput) -> AppointmentSyncLog:
        change_key = self.build_change_key(data)
        sync_log = self.db.query(AppointmentSyncLog). filter_by(change_key = change_key).first()

        if not sync_log:
            sync_log = AppointmentSyncLog(
                clinic_id = data.clinic_id,
                inbound_event_id = data.inbound_event_id,
                pat_id = data.pat_id,
                appointment_id= data.appointment_id,
                contact_id=encrypt_secret(data.contact_id),
                event_id=data.event_id,
                apt_num=data.apt_num,
                patient_name=encrypt_secret(data.patient_name) if data.patient_name else None,
                patient_name_search= self.normalize_search_text(data.patient_name),
                direction=data.direction,
                appointment_status=data.appointment_status,
                sync_status=SyncStatus.QUEUED,
                change_key=change_key,
                attempt_count=0,
                payload=encrypt_json_secret(data.payload),
                reason=None,
                completed_at=None,
            )
            self.db.add(sync_log)
            self.db.commit()
            self.db.refresh(sync_log)
            self.publish_change(sync_log)
        return sync_log
        

    def mark_processing(self, sync_log: AppointmentSyncLog) -> AppointmentSyncLog:
        sync_log.sync_status = SyncStatus.PROCESSING
        sync_log.reason = None
        sync_log.completed_at = None
        sync_log.attempt_count += 1

        self.db.commit()
        self.db.refresh(sync_log)
        self.publish_change(sync_log)
        return sync_log
    
    def mark_operation(self, sync_log: AppointmentSyncLog, *, operation: str) -> AppointmentSyncLog:
        sync_log.operation = operation
        self.db.commit()
        self.db.refresh(sync_log)
        self.publish_change(sync_log)
        return sync_log
    


    def mark_success(self, sync_log: AppointmentSyncLog, *, reason: str ="Sync completed successfully", appointment_id : Optional[uuid.UUID]= None,  operation: str, pat_id: Optional[uuid.UUID]= None, apt_num: Optional[int]= None) -> AppointmentSyncLog:
        sync_log.sync_status = SyncStatus.PROCESSED
        if operation is not None:
            sync_log.operation = operation
        sync_log.reason = reason
        sync_log.operation = operation
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.appointment_id = appointment_id or sync_log.appointment_id
        sync_log.pat_id = pat_id or sync_log.pat_id
        sync_log.apt_num = apt_num or sync_log.apt_num

        self.db.commit()
        self.db.refresh(sync_log)
        self.publish_change(sync_log)
        return sync_log 
    
    def mark_failure(self, sync_log: AppointmentSyncLog, *, reason: str, should_retry: bool, operation: str | None) -> AppointmentSyncLog:
        sync_log.sync_status = (
            SyncStatus.RETRYING if should_retry else SyncStatus.FAILED
        )
        sync_log.reason = reason
        if operation is not None: 
            sync_log.operation = operation 
        if not should_retry:
            sync_log.completed_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(sync_log)
        self.publish_change(sync_log)
        return sync_log

