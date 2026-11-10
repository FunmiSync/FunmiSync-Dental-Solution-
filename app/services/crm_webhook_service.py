"""Accept CRM webhooks: record the event, create its sync log, and queue the job."""
import json
import logging
from uuid import UUID, uuid4

from rq import Retry
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.infrastructure.queue import appointments_queue, async_redis
from app.infrastructure.workers.appointments import process_crm_load_job
from app.models.entities import InboundEvent, RegisteredClinics, SyncDirection
from app.security.encryption import decode_secret, encrypt_json_secret, encrypt_secret, hash_lookup
from app.security.webhook_secret import WEBHOOK_SECRET_HEADER
from app.services.appointment_sync_log_helper import AppointmentSyncLogService, SyncLogInput
from app.services.clinic_health import mark_webhook_auth_failed, reset_webhook_failure_after_success
from app.services.errors import Conflict, NotFound, OperationFailed, PermissionDenied

logger = logging.getLogger(__name__)

DUPLICATE_WINDOW_SECONDS = 300
JOB_RETRY = {"max": 3, "interval": [60, 120, 300]}


async def get_webhook_clinic(db: AsyncSession, *, clinic_id: UUID, crm_type: str) -> RegisteredClinics:
    logger.info("Webhook received", extra={"clinic_id": str(clinic_id), "crm_type": crm_type})
    clinic = await db.scalar(select(RegisteredClinics).filter_by(id=clinic_id).limit(1))
    if not clinic:
        logger.warning("Webhook for unknown clinic", extra={"clinic_id": str(clinic_id), "crm_type": crm_type})
        raise NotFound("clinic not found wrong webhook url ")

    if clinic.crm_type.lower() != crm_type.lower():
        raise PermissionDenied("incorrect webhook url")
    return clinic


async def record_webhook_auth_failure(db: AsyncSession, clinic: RegisteredClinics, *, crm_type: str) -> None:
    """Count a failed secret check so clinic health can flag a misconfigured CRM."""
    log_ctx = {"clinic_id": str(clinic.id), "crm_type": crm_type}
    logger.warning("Webhook secret validation failed", extra=log_ctx)
    mark_webhook_auth_failed(clinic)
    try:
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        logger.exception("Failed to persist webhook auth failure state", extra=log_ctx)
        raise OperationFailed("Unable to update webhook failure state") from exc
    if clinic.dso_id:
        await invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)


async def accept_webhook_event(db: AsyncSession, clinic: RegisteredClinics, *, crm_type: str, payload: dict) -> dict:
    logger.info("Webhook secret validation succeeded", extra={"clinic_id": str(clinic.id), "crm_type": crm_type})
    reset_webhook_failure_after_success(clinic)

    if clinic.is_disabled:
        raise PermissionDenied("Clinic is disabled")

    event_id = payload.get("event_id")
    log_ctx = {"clinic_id": str(clinic.id), "crm_type": crm_type, "event_id": event_id}

    # Claim this exact payload for a short window so CRM retries are not processed twice.
    redis_key = f"webhook_processing:{clinic.id}:{hash_lookup(json.dumps(payload, sort_keys=True))}"
    if not await async_redis.set(redis_key, "processing", ex=DUPLICATE_WINDOW_SECONDS, nx=True):
        raise Conflict("Duplicate Webhook")

    event = await _save_inbound_event(db, clinic, crm_type=crm_type, payload=payload, redis_key=redis_key, log_ctx=log_ctx)
    if clinic.dso_id:
        await invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)

    sync_log = await _create_sync_log(db, clinic, event, payload=payload, redis_key=redis_key, log_ctx=log_ctx)
    job = await _enqueue_sync_job(db, clinic, event, crm_type=crm_type, payload=payload, sync_log_id=sync_log.id,
                                  redis_key=redis_key, log_ctx=log_ctx)

    return {
        "status": 202,
        "job_id": job.id,
        "message": "Webhook queued successfully",
        "clinic": clinic.id,
        "crm_type": crm_type,
    }


def webhook_config_for(clinic: RegisteredClinics) -> dict:
    header_value = decode_secret(clinic.webhook_secret)
    if header_value is None:
        logger.error("Webhook secret could not be decoded", extra={"clinic_id": str(clinic.id)})
        raise OperationFailed("Webhook secret could not be decoded")
    return {
        "webhook_url": f"{settings.backend_base_url}/webhook/{clinic.crm_type}/{clinic.id}",
        "header_name": WEBHOOK_SECRET_HEADER,
        "header_value": header_value,
    }


# ------------------------------------------------------------- pipeline

async def _save_inbound_event(db: AsyncSession, clinic: RegisteredClinics, *, crm_type: str, payload: dict,
                              redis_key: str, log_ctx: dict) -> InboundEvent:
    event = InboundEvent(
        clinic_id=clinic.id,
        crm_type=crm_type,
        event_id=payload.get("event_id"),
        contact_id=encrypt_secret(payload.get("contact_id") or ""),
        payload=encrypt_json_secret(payload),
        processing_status="received",
    )
    try:
        db.add(event)
        await db.commit()
        await db.refresh(event)
    except SQLAlchemyError as exc:
        await db.rollback()
        await async_redis.delete(redis_key)
        logger.exception("Failed to persist inbound webhook event", extra=log_ctx)
        raise OperationFailed("Unable to persist inbound webhook event") from exc
    return event


async def _create_sync_log(db: AsyncSession, clinic: RegisteredClinics, event: InboundEvent, *, payload: dict,
                           redis_key: str, log_ctx: dict):
    patient_name = f"{payload.get('first_name', '')} {payload.get('last_name', '')}".strip() or None
    sync_input = SyncLogInput(
        clinic_id=clinic.id,
        inbound_event_id=event.id,
        pat_id=None,
        appointment_id=None,
        contact_id=payload.get("contact_id") or "",
        event_id=payload.get("event_id"),
        apt_num=None,
        patient_name=patient_name,
        date_str=payload.get("Date", ""),
        start_str=payload.get("start_time", ""),
        end_str=payload.get("end_time", ""),
        appointment_status=payload.get("status", ""),
        direction=SyncDirection.CRM_TO_OD,
        payload=payload,
    )
    try:
        return await AppointmentSyncLogService(db).get_or_create_sync_log(sync_input)
    except Exception as exc:
        await db.rollback()
        await _mark_event_failed(db, event, status="failed", reason=str(exc),
                           log_message="Failed to persist sync log creation failure on inbound event", log_ctx=log_ctx)
        await async_redis.delete(redis_key)
        logger.exception("Failed to create sync log for inbound webhook event", extra=log_ctx)
        raise OperationFailed("Unable to create sync log") from exc


async def _enqueue_sync_job(db: AsyncSession, clinic: RegisteredClinics, event: InboundEvent, *, crm_type: str,
                            payload: dict, sync_log_id: UUID, redis_key: str, log_ctx: dict):
    job_id = str(uuid4())
    log_ctx = {**log_ctx, "job_id": job_id}

    # Store the job id before enqueueing so a worker can always be traced to its event.
    try:
        event.job_id = job_id
        event.failure_reason = None
        await db.commit()
    except SQLAlchemyError as exc:
        await db.rollback()
        await async_redis.delete(redis_key)
        logger.exception("Failed to persist webhook job identifier", extra=log_ctx)
        raise OperationFailed("Unable to persist webhook job identifier") from exc

    try:
        # RQ's Redis client is synchronous; enqueue in a worker thread.
        job = await run_in_threadpool(
            appointments_queue.enqueue,
            process_crm_load_job,
            clinic.id,
            crm_type,
            {"encrypted_payload": encrypt_json_secret(payload)},
            sync_log_id,
            job_id=job_id,
            retry=Retry(**JOB_RETRY),
        )
    except Exception as exc:
        await async_redis.delete(redis_key)
        await _mark_event_failed(db, event, status="enqueue_failed", reason=str(exc),
                           log_message="Failed to persist enqueue failure state", log_ctx=log_ctx)
        logger.exception("Failed to enqueue webhook job", extra=log_ctx)
        raise OperationFailed("Unable to queue webhook job") from exc

    try:
        event.processing_status = "queued"
        await db.commit()
    except SQLAlchemyError:
        # The job is already queued; a stale status must not fail the webhook.
        await db.rollback()
        logger.exception("Webhook job queued but queued status could not be persisted", extra=log_ctx)
    return job


async def _mark_event_failed(db: AsyncSession, event: InboundEvent, *, status: str, reason: str, log_message: str, log_ctx: dict) -> None:
    # Write-only: safe even if a rollback just expired the event row.
    event.processing_status = status
    event.failure_reason = reason
    try:
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        logger.exception(log_message, extra=log_ctx)
