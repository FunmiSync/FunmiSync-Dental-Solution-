from fastapi import  APIRouter, Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from core.queue import async_redis
from rq import Retry
from core.database import get_db
from core.queue import appointments_queue
from core.models import RegisteredClinics
from core.schemas import Webhook_requests, webhook_response
from workers.workers import process_crm_load_job
from infra.webhook_secret import WEBHOOK_SECRET_HEADER, verify_webhook_secret_header
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
    contact_id = payload_dict.get("contact_id")

    #idempotency to avoid duplicate 
    redis_key = f"webhook processing: {event_id}:{contact_id}"
    if await async_redis.exists(redis_key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail = "Dupliacte Webhook")
    else:
        await async_redis.setex(redis_key, 300, "processing")

    retry_cfg = Retry(
        max=3, 
        interval=[60, 120, 300] 
    )
    #queue the job 
    job = appointments_queue.enqueue(process_crm_load_job, clinic_id, crm_type, payload_dict, retry =  retry_cfg)

    return {"status" : status.HTTP_200_OK ,
            "payload": payload,
            "job_id" : job.id,
            "message": "Webhook processed successfully",
            "clinic": clinic_id,
            "crm_type": crm_type}



        
        


    

    
