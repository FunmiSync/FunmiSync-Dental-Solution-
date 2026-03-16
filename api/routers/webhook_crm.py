from fastapi import  APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from core.queue import async_redis
from rq import Retry
from core.database import get_db
from core.queue import appointments_queue
from core.models import RegisteredClinics, InboundEvent, SyncDirection
from core.schemas import Webhook_requests, webhook_response
from workers.workers import process_crm_load_job
from infra.webhook_secret import WEBHOOK_SECRET_HEADER, verify_webhook_secret_header
from infra.appointment_sync_log_helper import AppointmentSyncLogService, SyncLogInput
import logging
from uuid import UUID


router= APIRouter( 
    prefix= "/webhook",
    tags= ["CRM Webhooks"]
    )
logger = logging.getLogger(__name__)

@router.post("/{crm_type}/{clinic_id}", status_code=202, response_model = webhook_response)
async def webhooks(crm_type: str, clinic_id: UUID, request: Request, payload: Webhook_requests , db: Session = Depends(get_db)):
     # check if clinic is there 
    logger.info(f"webhook received for clinic  {clinic_id}")
    clinic = db.query(RegisteredClinics).filter_by(id=clinic_id).first()
    if not clinic:
        logger.warning(f"Webhook for invalid clinic_id={clinic_id} | crm={crm_type}")
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail = "clinic not found wrong webhook url ")

    # checks if the crm supported is the one returned 
    if  clinic.crm_type.lower() != crm_type.lower():
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail= "incorrect webhook url")
    
    Provided_secret = request.headers.get(WEBHOOK_SECRET_HEADER)
    verify_webhook_secret_header(provided_secret= Provided_secret, stored_secret_encrypted= clinic.webhook_secret)

    #extract payload for redis use 
    payload_dict = payload.model_dump()
    event_id = payload_dict.get("event_id")
    contact_id = payload_dict.get("contact_id") or ""
    date_str = payload_dict.get("Date", "")
    start_str = payload_dict.get("start_time", "")
    end_str = payload_dict.get("end_time", "")
    appointment_status = payload_dict.get("status", "")
    patient_name = f"{payload_dict.get('first_name', '')} {payload_dict.get('last_name', '')}".strip() or None

    #idempotency to avoid duplicate 
    redis_key = f"webhook processing: {event_id}:{contact_id}"
    if await async_redis.exists(redis_key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail = "Dupliacte Webhook")
    else:
        await async_redis.setex(redis_key, 300, "processing")

    event = InboundEvent(
    clinic_id=clinic.id,
    crm_type=crm_type,
    event_id=payload_dict.get("event_id"),
    contact_id=payload_dict.get("contact_id"),
    payload=payload_dict,
    processing_status="received",
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    sync_log_service = AppointmentSyncLogService(db)
    sync_input = SyncLogInput(
        clinic_id = clinic_id,
        inbound_event_id= event.id,
        pat_id = None,
        appointment_id = None,
        contact_id = contact_id,
        event_id = event_id,
        apt_num = None,
        patient_name= patient_name,
        date_str = date_str,
        start_str = start_str,
        end_str= end_str,
        appointment_status= appointment_status,
        direction = SyncDirection.CRM_TO_OD,
        payload = payload_dict
    )

    sync_log = sync_log_service.get_or_create_sync_log(sync_input)

    retry_cfg = Retry(
        max=3, 
        interval=[60, 120, 300] 
    )
    #queue the job 
    job = appointments_queue.enqueue(process_crm_load_job, clinic_id, crm_type, payload_dict, sync_log.id, retry =  retry_cfg)

    event.job_id = job.id
    event.processing_status = "queued"
    db.commit()

    return {"status" : status.HTTP_200_OK ,
            "payload": payload,
            "job_id" : job.id,
            "message": "Webhook processed successfully",
            "clinic": clinic_id,
            "crm_type": crm_type
            }



        
        


    

    
