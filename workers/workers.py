from core.database import SessionLocal
from core.models import Patients, RegisteredClinics, InboundEvent, AppointmentSyncLog,Appointments
from sdk.opendental_sdk import openDentalApi
from fastapi import HTTPException
from core.schemas import patient_model
from infra.appointment_service import AppointmentService
from infra.appointment_sync_log_helper import AppointmentSyncLogService
from infra.patient_creation import PatientService
from core.schemas import AppointmentRequest
from sqlalchemy.exc import SQLAlchemyError
from core.circuti_breaker import circuit_breaker_open_error
from rq import get_current_job
from datetime import datetime, timezone
import logging
import asyncio
from uuid import UUID

logger = logging.getLogger(__name__)


def _mark_event_processing(db, current_job_id: str | None):
    if not current_job_id:
        return None

    event = db.query(InboundEvent).filter(InboundEvent.job_id == current_job_id).first()
    if event:
        event.attempt_count += 1
        event.processing_status = "processing"
        event.failure_reason = None
        event.processed_at = None
        db.commit()
    return event


def _mark_event_result(db, current_job_id: str | None, job, *, status: str, failure_reason: str | None = None):
    if not current_job_id:
        return

    event = db.query(InboundEvent).filter(InboundEvent.job_id == current_job_id).first()
    if not event:
        return

    event.processing_status = status
    event.failure_reason = failure_reason
    if status in {"processed", "failed"}:
        event.processed_at = datetime.now(timezone.utc)
    db.commit()


def _mark_event_retry_or_failure(db, current_job_id: str | None, job, error: Exception):
    retries_left = job.retries_left if job else None
    status = "retrying" if retries_left and retries_left > 0 else "failed"
    _mark_event_result(
        db,
        current_job_id,
        job,
        status=status,
        failure_reason=str(error),
    )

def _mark_sync_log_retry_or_failure(sync_log_service: AppointmentSyncLogService, sync_log: AppointmentSyncLog | None, job, error: Exception,) -> None:
    if not sync_log:
        return

    should_retry = bool(job and job.retries_left and job.retries_left > 0)

    sync_log_service.mark_failure(
        sync_log,
        reason=str(error),
        should_retry=should_retry,
    )



def process_crm_load_job(clinic_id : UUID, crm_type: str, payload: dict,sync_log_id: UUID):
    return asyncio.run(process_crm_load(clinic_id, crm_type, payload, sync_log_id))


async def process_crm_load(clinic_id: UUID, crm_type: str, payload: dict, sync_log_id: UUID):
    db = SessionLocal()
    job = get_current_job()
    current_job_id = job.id if job else None 

    sync_log = db.query(AppointmentSyncLog).filter_by(id = sync_log_id).first()
    sync_log_service= AppointmentSyncLogService(db)
    try:
        _mark_event_processing(db, current_job_id)
        if sync_log:
            sync_log_service.mark_processing(sync_log)

        logger.info("CRM job started", extra={
            "clinic_id": clinic_id,
            "crm_type": crm_type,
            "event_id": payload.get("event_id"),
            "contact_id": payload.get("contact_id"),
        })

        clinic = db.query(RegisteredClinics).filter_by(id=clinic_id).first()
        if not clinic:
            raise ValueError("clinic id  {clinic} not found ")

        clinic_timezone = clinic.clinic_timezone
        
        patient_data = patient_model(
            FName=payload.get("first_name", ""),
            LName=payload.get("last_name", ""),
            Gender=payload.get("Gender"),
            Address=payload.get("Address"),
            Birthdate=payload.get("BirthDate"),
            WirelessPhone=payload.get("WirelessPhone"),
            Email=payload.get("Email"),
        )
        logger.info("Patient payload mapped", extra={
            "clinic_id": clinic_id,
            "event_id": payload.get("event_id"),
            "contact_id": payload.get("contact_id"),
            "has_fname": bool(patient_data.FName),
            "has_lname": bool(patient_data.LName),
            "has_birthdate": bool(patient_data.Birthdate),
        })

        commslog = payload.get("commslog", "")
        date_str = payload.get("Date", "")
        start_str = payload.get("start_time", "")
        end_str = payload.get("end_time", "")
        status = payload.get("status", "")
        calendar_id = payload.get("calendar_id", "")
        event_id = payload.get("event_id", "")
        contact_id = payload.get("contact_id", "")
        Note = payload.get("Notes", "")
        pop_up = payload.get("pop_up", "")



        od = openDentalApi(clinic_id)
        pat = PatientService(db, od, clinic_id)
        result = await pat.resolve_patnum(pat=patient_data, contact_id=contact_id)
        if result is None:
            raise ValueError("No contact id could not be resolved ")

        pat_id, pat_num = result
        logger.info("Resolved patient identifiers", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "pat_id": pat_id,
            "pat_num": pat_num,
        })

        appointment_req = AppointmentRequest(
            date_str=date_str,
            start_str=start_str,
            end_str=end_str,
            status=status,
            calendar_id=calendar_id,
            event_id=event_id,
            contact_id=contact_id,
            Note=Note,
            pop_up=pop_up,
            commslog=commslog,
            pat_Num=pat_num,
            clinic_timezone=clinic_timezone,
            pat_id=pat_id,
        )
        logger.info("Appointment request prepared", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "calendar_id": calendar_id,
            "status": status,
            "date_str": date_str,
            "start_str": start_str,
            "end_str": end_str,
        })

        appointment_service = AppointmentService(db=db, od_client=od, clinic=clinic)
        apt_num = await appointment_service.book(appointment_req)

        if not apt_num:
            logger.error("Appointment Failed to get Booked", extra={
                "clinic": clinic.id,
                "pat_ num": pat_num,
                "contact_id": contact_id,
            })
            raise ValueError("Appointment booking Failed ")

        logger.info("Appointment booked", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "apt_num": apt_num,
        })

        #Fill the inbound event table and also the sync_log table on success
        _mark_event_result(db, current_job_id, job, status="processed")
        appointment_row = db.query(Appointments).filter_by(clinic_id = clinic_id, event_id= event_id).first()
        appointment_id = appointment_row.id if appointment_row else None
        if sync_log:
            sync_log_service.mark_success(sync_log,reason="Created in OpenDental",appointment_id= appointment_id, pat_id=pat_id, apt_num=apt_num)
            
    except circuit_breaker_open_error as e:
        db.rollback()
        logger.warning(
            "Too many failures; circuit breaker is open",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_id": payload.get("contact_id"),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e)
        raise ValueError("Opendental is down please try again later")

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(
            f"Error processing patient: {e}",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_id": payload.get("contact_id"),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e)
        raise HTTPException(status_code=500, detail="Database error occurred")

    except Exception as e:
        db.rollback()
        logger.exception(
            "Unexpected error while processing CRM load",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_id": payload.get("contact_id"),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e)
        raise

    finally:
        db.close()