import logging
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from rq import Retry
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.security.encryption import encrypt_json_secret, encrypt_secret, hash_lookup
from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.infrastructure.database import get_db
from app.models.entities import RegisteredClinics, InboundEvent, SyncDirection
from app.infrastructure.queue import async_redis, appointments_queue
from app.schemas.api import Webhook_requests, webhook_response
from app.services.appointment_sync_log_helper import AppointmentSyncLogService, SyncLogInput
from app.services.clinic_health import mark_webhook_auth_failed, reset_webhook_failure_after_success
from app.security.webhook_secret import WEBHOOK_SECRET_HEADER, verify_webhook_secret_header
from app.infrastructure.workers.appointments import process_crm_load_job


router = APIRouter(
    prefix="/webhook",
    tags=["CRM Webhooks"]
)
logger = logging.getLogger(__name__)


@router.post("/{crm_type}/{clinic_id}", status_code=202, response_model=webhook_response)
async def webhooks(
    crm_type: str,
    clinic_id: UUID,
    request: Request,
    payload: Webhook_requests,
    db: Session = Depends(get_db),
):
    logger.info(f"webhook received for clinic  {clinic_id}")
    clinic = db.query(RegisteredClinics).filter_by(id=clinic_id).first()
    if not clinic:
        logger.warning(f"Webhook for invalid clinic_id={clinic_id} | crm={crm_type}")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="clinic not found wrong webhook url ")

    if clinic.crm_type.lower() != crm_type.lower():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="incorrect webhook url")

    provided_secret = request.headers.get(WEBHOOK_SECRET_HEADER)
    try:
        verify_webhook_secret_header(
            provided_secret=provided_secret, 
            stored_secret_encrypted=clinic.webhook_secret
            )
    except HTTPException:
        logger.warning(
        "Webhook secret validation failed",
        extra={
            "clinic_id": str(clinic.id),
            "crm_type": crm_type,
        },
    )
        mark_webhook_auth_failed(clinic)
        
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "Failed to persist webhook auth failure state",
                extra={
                    "clinic_id": str(clinic.id),
                    "crm_type": crm_type,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Unable to update webhook failure state",
            )
        if clinic.dso_id:
            invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)

        raise

    logger.info(
        "Webhook secret validation succeeded",
        extra={
            "clinic_id": str(clinic.id),
            "crm_type": crm_type,
        },
    )

    reset_webhook_failure_after_success(clinic)

    if clinic.is_disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Clinic is disabled")

    payload_dict = payload.model_dump()
    event_id = payload_dict.get("event_id")
    contact_id = payload_dict.get("contact_id") or ""
    date_str = payload_dict.get("Date", "")
    start_str = payload_dict.get("start_time", "")
    end_str = payload_dict.get("end_time", "")
    appointment_status = payload_dict.get("status", "")
    patient_name = f"{payload_dict.get('first_name', '')} {payload_dict.get('last_name', '')}".strip() or None

    redis_key = f"webhook_processing:{clinic_id}:{hash_lookup(json.dumps(payload_dict, sort_keys=True))}"
    claimed = await async_redis.set(redis_key, "processing", ex=300, nx=True)
    if not claimed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Duplicate Webhook")

    event = InboundEvent(
        clinic_id=clinic.id,
        crm_type=crm_type,
        event_id=payload_dict.get("event_id"),
        contact_id=encrypt_secret(contact_id),
        payload=encrypt_json_secret(payload_dict),
        processing_status="received",
    )

    try:
        db.add(event)
        db.commit()
        db.refresh(event)
    except SQLAlchemyError:
        db.rollback()
        await async_redis.delete(redis_key)
        logger.exception(
            "Failed to persist inbound webhook event",
            extra={
                "clinic_id": str(clinic.id),
                "crm_type": crm_type,
                "event_id": event_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to persist inbound webhook event",
        )
    
    if clinic.dso_id:
        invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)

    sync_log_service = AppointmentSyncLogService(db)
    sync_input = SyncLogInput(
        clinic_id=clinic_id,
        inbound_event_id=event.id,
        pat_id=None,
        appointment_id=None,
        contact_id=contact_id,
        event_id=event_id,
        apt_num=None,
        patient_name=patient_name,
        date_str=date_str,
        start_str=start_str,
        end_str=end_str,
        appointment_status=appointment_status,
        direction=SyncDirection.CRM_TO_OD,
        payload=payload_dict,
    )

    try:
        sync_log = sync_log_service.get_or_create_sync_log(sync_input)
    except Exception as exc:
        db.rollback()
        event.processing_status = "failed"
        event.failure_reason = str(exc)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "Failed to persist sync log creation failure on inbound event",
                extra={
                    "clinic_id": str(clinic.id),
                    "crm_type": crm_type,
                    "event_id": event_id,
                },
            )
        await async_redis.delete(redis_key)
        logger.exception(
            "Failed to create sync log for inbound webhook event",
            extra={
                "clinic_id": str(clinic.id),
                "crm_type": crm_type,
                "event_id": event_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to create sync log",
        ) from exc

    retry_cfg = Retry(
        max=3,
        interval=[60, 120, 300]
    )
    job_id = str(uuid4())

    try:
        event.job_id = job_id
        event.failure_reason = None
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        await async_redis.delete(redis_key)
        logger.exception(
            "Failed to persist webhook job identifier",
            extra={
                "clinic_id": str(clinic.id),
                "crm_type": crm_type,
                "event_id": event_id,
                "job_id": job_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to persist webhook job identifier",
        )

    try:
        job = appointments_queue.enqueue(
            process_crm_load_job,
            clinic_id,
            crm_type,
            {"encrypted_payload": encrypt_json_secret(payload_dict)},
            sync_log.id,
            job_id=job_id,
            retry=retry_cfg,
        )
    except Exception as exc:
        await async_redis.delete(redis_key)
        event.processing_status = "enqueue_failed"
        event.failure_reason = str(exc)
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception(
                "Failed to persist enqueue failure state",
                extra={
                    "clinic_id": str(clinic.id),
                    "crm_type": crm_type,
                    "event_id": event_id,
                    "job_id": job_id,
                },
            )
        logger.exception(
            "Failed to enqueue webhook job",
            extra={
                "clinic_id": str(clinic.id),
                "crm_type": crm_type,
                "event_id": event_id,
                "job_id": job_id,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to queue webhook job",
        ) from exc

    try:
        event.processing_status = "queued"
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception(
            "Webhook job queued but queued status could not be persisted",
            extra={
                "clinic_id": str(clinic.id),
                "crm_type": crm_type,
                "event_id": event_id,
                "job_id": job_id,
            },
        )

    return {
        "status": status.HTTP_202_ACCEPTED,
        "job_id": job.id,
        "message": "Webhook queued successfully",
        "clinic": clinic_id,
        "crm_type": crm_type,
    }
