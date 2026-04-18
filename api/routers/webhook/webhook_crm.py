from fastapi import APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from rq import Retry
import logging
from uuid import UUID
from sqlalchemy.exc import SQLAlchemyError
from infra.clinic_health import mark_webhook_auth_failed,reset_webhook_failure_after_success
from auth.security import encrypt_json_secret, encrypt_secret, hash_lookup
from caches.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from core.database import get_db
from core.models import RegisteredClinics, InboundEvent, SyncDirection
from core.queue import async_redis, appointments_queue
from core.schemas import Webhook_requests, webhook_response
from infra.appointment_sync_log_helper import AppointmentSyncLogService, SyncLogInput
from infra.webhook_secret import WEBHOOK_SECRET_HEADER, verify_webhook_secret_header
from workers.workers import process_crm_load_job


router = APIRouter(
    prefix="/webhook",
    tags=["CRM Webhooks"]
)
logger = logging.getLogger(__name__)


@router.post("/{crm_type}/{clinic_id}", status_code=202, response_model=webhook_response)
async def webhooks(crm_type: str, clinic_id: UUID, request: Request, payload: Webhook_requests, db: Session = Depends(get_db)):
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
   
    payload_dict = payload.model_dump()
    event_id = payload_dict.get("event_id")
    contact_id = payload_dict.get("contact_id") or ""
    date_str = payload_dict.get("Date", "")
    start_str = payload_dict.get("start_time", "")
    end_str = payload_dict.get("end_time", "")
    appointment_status = payload_dict.get("status", "")
    patient_name = f"{payload_dict.get('first_name', '')} {payload_dict.get('last_name', '')}".strip() or None

    redis_key = f"webhook_processing:{event_id}:{hash_lookup(contact_id)}"
    if await async_redis.exists(redis_key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Dupliacte Webhook")
    await async_redis.setex(redis_key, 300, "processing")

    event = InboundEvent(
        clinic_id=clinic.id,
        crm_type=crm_type,
        event_id=payload_dict.get("event_id"),
        contact_id=encrypt_secret(contact_id),
        payload=encrypt_json_secret(payload_dict),
        processing_status="received",
    )

    db.add(event)
    try:
        db.commit()
        db.refresh(event)
    
    except SQLAlchemyError:
        db.rollback()
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

    sync_log = sync_log_service.get_or_create_sync_log(sync_input)

    retry_cfg = Retry(
        max=3,
        interval=[60, 120, 300]
    )
    job = appointments_queue.enqueue(
        process_crm_load_job,
        clinic_id,
        crm_type,
        payload_dict,
        sync_log.id,
        retry=retry_cfg,
    )

    event.job_id = job.id
    event.processing_status = "queued"
    db.commit()

    return {
        "status": status.HTTP_202_ACCEPTED,
        "job_id": job.id,
        "message": "Webhook processed successfully",
        "clinic": clinic_id,
        "crm_type": crm_type,
    } 