from app.infrastructure.database import SessionLocal
from app.models.entities import Patients, RegisteredClinics, InboundEvent, AppointmentSyncLog, Appointments, SyncFailureSource
from app.infrastructure.providers.opendental import openDentalApi, OpenDentalAuthError, OpenDentalUnavailableError, OpenDentalRequestError
from fastapi import HTTPException
from app.schemas.api import patient_model
from app.services.appointment_service import AppointmentService, CustomerConfigurationSyncError, OpenDentalSyncError, InternalSyncError
from app.services.appointment_sync_log_helper import AppointmentSyncLogService
from app.services.patient_creation import PatientService
from app.security.encryption import fingerprint_value
from app.schemas.api import AppointmentRequest
from sqlalchemy.exc import SQLAlchemyError
from app.infrastructure.circuit_breaker import CircuitBreakerOpenError
from rq import get_current_job
from datetime import datetime, timezone
import logging
import asyncio
from uuid import UUID
from app.infrastructure.clinic_lock import clinic_sync_lock
from app.security.encryption import decode_json_secret

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

def _mark_sync_log_retry_or_failure(sync_log_service: AppointmentSyncLogService, sync_log: AppointmentSyncLog | None, job, error: Exception, *, failure_source: SyncFailureSource, operation: str| None = None) -> None:
    if not sync_log:
        return

    should_retry = bool(job and job.retries_left and job.retries_left > 0)

    sync_log_service.mark_failure(
        sync_log,
        reason=str(error),
        should_retry=should_retry,
        operation= operation,
        failure_source = failure_source
    )



def process_crm_load_job(clinic_id : UUID, crm_type: str, payload: dict, sync_log_id: UUID):
    return asyncio.run(process_crm_load(clinic_id, crm_type, payload, sync_log_id))


async def process_crm_load(clinic_id: UUID, crm_type: str, payload: dict, sync_log_id: UUID):
    if "encrypted_payload" in payload:
        payload = decode_json_secret(payload["encrypted_payload"])
        if not isinstance(payload, dict):
            raise ValueError("Invalid encrypted job payload")
    db = SessionLocal()
    lock = None
    job = get_current_job()
    current_job_id = job.id if job else None 

    sync_log_service= AppointmentSyncLogService(db)
    try:
        sync_log = db.query(AppointmentSyncLog).filter_by(id = sync_log_id).first()
        if not sync_log:
            raise ValueError("Sync log not found for queued job")
        _mark_event_processing(db, current_job_id)
        if sync_log:
            sync_log_service.mark_processing(sync_log)

        logger.info("CRM job started", extra={
            "clinic_id": clinic_id,
            "crm_type": crm_type,
            "event_id": payload.get("event_id"),
            "contact_ref": fingerprint_value(payload.get("contact_id")),
        })

        clinic = db.query(RegisteredClinics).filter_by(id=clinic_id).first()
        if not clinic:
            raise ValueError("clinic id  {clinic} not found ")
        if clinic.is_disabled:
            # A disabled clinic is an intentional stop, not a retryable or billable failure.
            _mark_event_result(db, current_job_id, job, status="failed", failure_reason="Clinic is disabled")
            sync_log_service.mark_failure(
                sync_log, reason="Clinic is disabled", should_retry=False, operation=None,
                failure_source=SyncFailureSource.CUSTOMER_CONFIGURATION, billable=False,
            )
            return {"status": "skipped", "reason": "Clinic is disabled"}

        pending_lock = clinic_sync_lock(db, clinic_id)
        pending_lock.__enter__()
        lock = pending_lock

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
            "contact_ref": fingerprint_value(payload.get("contact_id")),
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
        contact_ref = fingerprint_value(contact_id)
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
            "contact_ref": contact_ref,
            "has_patient": True,
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
            "contact_ref": contact_ref,
            "calendar_id": calendar_id,
            "status": status,
            "date_str": date_str,
            "start_str": start_str,
            "end_str": end_str,
        })

        appointment_service = AppointmentService(db=db, od_client=od, clinic=clinic)
        booking = await appointment_service.book(appointment_req, sync_log_service=sync_log_service, sync_log=sync_log )

        if not booking:
            logger.error("Appointment Failed to get Booked", extra={
                "clinic": clinic.id,
                "contact_ref": contact_ref,
            })
            raise ValueError("Appointment booking Failed ")
        
        apt_num = booking.apt_num
        logger.info("Appointment booked", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_ref": contact_ref,
            "apt_num": apt_num,
        })

        #Fill the inbound event table and also the sync_log table on success
        _mark_event_result(db, current_job_id, job, status="processed")
        appointment_row = db.query(Appointments).filter_by(clinic_id = clinic_id, event_id= event_id).first()
        appointment_id = appointment_row.id if appointment_row else None
        if sync_log:
            sync_log_service.mark_success(sync_log, reason="Created in opendental Successfully", operation= booking.action, appointment_id= appointment_id,  pat_id=pat_id, apt_num=apt_num)
            

    except CircuitBreakerOpenError as e:
        db.rollback()
        logger.warning(
            "Too many failures; circuit breaker is open",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e, failure_source = SyncFailureSource.OPEN_DENTAL )
        raise ValueError("Opendental is down please try again later")


    except (CustomerConfigurationSyncError, OpenDentalAuthError, OpenDentalRequestError) as e:
        db.rollback()
        logger.warning(
            "Customer configuration caused sync failure",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_result(db, current_job_id, job, status="failed", failure_reason=str(e))
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        if sync_log:
            sync_log_service.mark_failure(
                sync_log,
                reason=str(e),
                should_retry=False,
                operation=None,
                failure_source=SyncFailureSource.CUSTOMER_CONFIGURATION,
            )
        raise

    except (OpenDentalSyncError, OpenDentalUnavailableError) as e:
        db.rollback()
        logger.warning(
            "OpenDental caused sync failure",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(
            sync_log_service,
            sync_log,
            job,
            e,
            failure_source=SyncFailureSource.OPEN_DENTAL,
        )
        raise

    except InternalSyncError as e:
        db.rollback()
        logger.exception(
            "Internal sync processing failure",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(
            sync_log_service,
            sync_log,
            job,
            e,
            failure_source=SyncFailureSource.INTERNAL,
        )
        raise

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(
            f"Error processing patient: {e}",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e, failure_source= SyncFailureSource.INTERNAL)
        raise HTTPException(status_code=500, detail="Database error occurred")

    except Exception as e:
        db.rollback()
        logger.exception(
            "Unexpected error while processing CRM load",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_ref": fingerprint_value(payload.get("contact_id")),
            },
        )
        _mark_event_retry_or_failure(db, current_job_id, job, e)
        sync_log = db.query(AppointmentSyncLog).filter_by(id=sync_log_id).first()
        _mark_sync_log_retry_or_failure(sync_log_service, sync_log, job, e, failure_source= SyncFailureSource.UNKNOWN)
        raise

    finally:
        try:
            if lock is not None:
                lock.__exit__(None, None, None)
        finally:
            db.close()
