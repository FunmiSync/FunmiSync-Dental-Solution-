from datetime import datetime, timezone
from sqlalchemy.orm import Session
from dataclasses import dataclass
from typing import Any, Optional
from core.models import AppointmentSyncLog, SyncDirection,SyncStatus
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
        return (
                f"{data.clinic_id}|{data.contact_id}|{data.date_str}|"
                 f"{data.start_str}|{data.end_str}|"
        f"{data.appointment_status}|{data.direction.value}" 
        )

    
    def get_or_create_sync_log(self, data: SyncLogInput) -> AppointmentSyncLog:
        change_key = self.build_change_key(data)
        sync_log = self.db.query(AppointmentSyncLog). filter_by(change_key = change_key).first()

        if not sync_log:
            sync_log = AppointmentSyncLog(
                clinic_id = data.clinic_id,
                inbound_event_id = data.inbound_event_id,
                pat_id = data.pat_id,
                appointment_id= data.appointment_id,
                contact_id=data.contact_id,
                event_id=data.event_id,
                apt_num=data.apt_num,
                patient_name=data.patient_name,
                direction=data.direction,
                appointment_status=data.appointment_status,
                sync_status=SyncStatus.QUEUED,
                change_key=change_key,
                attempt_count=0,
                payload=data.payload,
                reason=None,
                completed_at=None,
            )
            self.db.add(sync_log)
            self.db.commit()
            self.db.refresh(sync_log)
        return sync_log
        

    def mark_processing(self, sync_log: AppointmentSyncLog) -> AppointmentSyncLog:
        sync_log.sync_status = SyncStatus.PROCESSING
        sync_log.reason = None
        sync_log.completed_at = None
        sync_log.attempt_count += 1

        self.db.commit()
        self.db.refresh(sync_log)
        return sync_log
    


    def mark_success(self, sync_log: AppointmentSyncLog, *, reason: str ="Sync completed successfully", appointment_id : Optional[uuid.UUID]= None, pat_id: Optional[uuid.UUID]= None, apt_num: Optional[int]= None) -> AppointmentSyncLog:
        sync_log.sync_status = SyncStatus.PROCESSED
        sync_log.reason = reason
        sync_log.completed_at = datetime.now(timezone.utc)
        sync_log.appointment_id = appointment_id or sync_log.appointment_id
        sync_log.pat_id = pat_id or sync_log.pat_id
        sync_log.apt_num = apt_num or sync_log.apt_num

        self.db.commit()
        self.db.refresh(sync_log)
        return sync_log 
    
    def mark_failure(self, sync_log: AppointmentSyncLog, *,reason: str, should_retry: bool,) -> AppointmentSyncLog:
        sync_log.sync_status = (
            SyncStatus.RETRYING if should_retry else SyncStatus.FAILED
        )
        sync_log.reason = reason

        if not should_retry:
            sync_log.completed_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(sync_log)
        return sync_log

