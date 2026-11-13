"""Work through each clinic's CRM events in the order they arrived. Every event is resolved.

Postgres holds the line: the webhook saves every event in inbound_events as "queued"
before it queues a job, and the job only says "clinic X has something waiting".
Whichever worker runs it takes clinic X's lock and works through X's waiting events,
oldest first. Several workers can serve different clinics at once, but never the same
clinic; a worker that finds X busy just leaves, because the one holding X reaches its
event too. A lost or repeated job therefore cannot lose or double an event.

No event is dropped, whatever its age. One that does not go through yet:
- OpenDental does not answer: the clinic's line pauses and tries again (2, 5, then 15
  minutes apart) for as long as it takes; this never counts as a retry. After an hour
  the clinic is alerted.
- A timed-out booking whose outcome is not settled: the event waits and is looked at
  again shortly (it settles within OPENDENTAL_NOT_DONE_AFTER); not counted either.
- Anything else, ours or the clinic's (rejected API keys, chair mapping, patient match,
  no free slot, a request OpenDental refuses, an outcome a person must settle): retried
  after 1 minute, 5 minutes, 30 minutes, 2 hours and 6 hours. A clinic problem alerts the
  clinic at the first failure (sync_alerts). If the fifth retry fails too, the event goes
  to the dead queue ("dead"): kept, never sent again on its own, and our team is alerted.
  `tools.sync_line replay` resends it once the cause is fixed.
A later change to the same appointment waits only behind short waits (an outage, a
timed-out booking being checked). Otherwise it goes through, and the older event closes
as superseded, because the newest change wins anyway.
A disabled clinic's webhooks are refused, so nothing new arrives; events already in its
line wait, untouched, until it is enabled again.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from rq import Retry
from sqlalchemy import exists, func, or_, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import aliased

from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.infrastructure.circuit_breaker import CircuitBreakerOpenError
from app.infrastructure.clinic_lock import ClinicBusyError, clinic_sync_lock
from app.infrastructure.database import reload_expired, worker_session
from app.infrastructure.providers.opendental import (
    OpenDentalAuthError, OpenDentalRequestError, OpenDentalUnavailableError, openDentalApi,
)
from app.infrastructure.queue import appointments_queue, async_redis
from app.models.entities import AppointmentSyncLog, Appointments, InboundEvent, RegisteredClinics, SyncFailureSource
from app.schemas.api import AppointmentRequest, patient_model
from app.security.encryption import decode_json_secret, fingerprint_value
from app.services import sync_alerts
from app.services.appointment_service import (
    OPENDENTAL_CREDENTIALS, OPENDENTAL_REFUSED, OPENDENTAL_UNREACHABLE, PATIENT_MATCH,
    AppointmentService, ClinicProblem, CustomerConfigurationSyncError, OpenDentalSyncError,
)
from app.services.appointment_sync_log_helper import AppointmentSyncLogService
from app.services.audit_logs import record_audit
from app.services.clinic_health import (
    as_utc, mark_od_auth_failed, mark_od_unreachable, opendental_paused_until, unreachable_too_long,
)
from app.services.external_operations import OutcomeNotKnownYet, ReconciliationRequired
from app.services.patient_creation import PatientMatchError, PatientService

logger = logging.getLogger(__name__)

WAITING = ("queued", "waiting", "retrying", "needs_attention", "processing")  # "processing": its worker died
DEAD = "dead"  # stopped after MAX_RETRIES retries; kept until tools.sync_line replay resends it
# Short waits keep a later change to the same appointment behind them; long ones do not.
HOLDS_LATER_CHANGES = ("queued", "waiting", "processing")
BATCH_SIZE = 20             # events per turn, then other clinics get the worker
MAX_ROUNDS = 5              # turns one job takes before going to the back of the queue
# Waits before each retry; after the last one fails, the event goes to the dead queue.
RETRY_AFTER = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30),
               timedelta(hours=2), timedelta(hours=6))
MAX_RETRIES = len(RETRY_AFTER)
LOOK_AGAIN_SOON = timedelta(minutes=2)    # a timed-out booking OpenDental may still show
JOB_RETRY = {"max": 3, "interval": [30, 60, 120]}  # only used when a worker dies mid-job
# RQ stops a job after 3 minutes by default; a full turn (up to MAX_ROUNDS x BATCH_SIZE events,
# a few OpenDental calls each) can take longer.
JOB_TIMEOUT = 15 * 60

OPENDENTAL_DOWN = (CircuitBreakerOpenError, OpenDentalSyncError, OpenDentalUnavailableError)
CLINIC_SIDE = (CustomerConfigurationSyncError, OpenDentalAuthError, OpenDentalRequestError)
# Alerts that close as soon as OpenDental answers this clinic again.
OPENDENTAL_ANSWERED = (OPENDENTAL_CREDENTIALS, OPENDENTAL_UNREACHABLE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ jobs

def work_through_clinic_job(clinic_id: UUID):
    return asyncio.run(_run_job(work_through_clinic(clinic_id)))


def process_crm_load_job(clinic_id: UUID, crm_type: str | None = None, payload: dict | None = None,
                         sync_log_id: UUID | None = None):
    """Jobs queued before the line existed still name this function. Their events were
    saved as queued, so they are in the line already: the job only wakes the clinic."""
    return work_through_clinic_job(clinic_id)


async def _run_job(work):
    try:
        return await work
    finally:
        # asyncio.run closes this loop after the job; drop Redis connections bound to it
        # so a worker that runs several jobs in one process never reuses a dead socket.
        await async_redis.connection_pool.disconnect()


async def wake(clinic_id: UUID, *, at: datetime | None = None) -> None:
    """Queue a job for the clinic's line, now or at a set time (needs `rq worker --with-scheduler`)."""
    try:
        if at is None:
            await asyncio.to_thread(appointments_queue.enqueue, work_through_clinic_job, clinic_id,
                                    retry=Retry(**JOB_RETRY), job_timeout=JOB_TIMEOUT)
        else:
            await asyncio.to_thread(appointments_queue.enqueue_at, at, work_through_clinic_job, clinic_id,
                                    retry=Retry(**JOB_RETRY), job_timeout=JOB_TIMEOUT)
    except Exception:
        # The events are safe in Postgres; the next webhook or `tools.sync_line wake` wakes the line.
        logger.exception("Could not queue a job for the clinic's line", extra={"clinic_id": str(clinic_id)})


async def wake_waiting_clinics(db) -> list[UUID]:
    """Queue a job for every enabled clinic with waiting events: the safety net for a lost job.
    Disabled clinics are skipped; their events wait until the clinic is enabled again."""
    clinic_ids = list((await db.scalars(
        select(InboundEvent.clinic_id).join(RegisteredClinics, RegisteredClinics.id == InboundEvent.clinic_id)
        .where(InboundEvent.processing_status.in_(WAITING), RegisteredClinics.is_disabled.is_(False))
        .distinct())).all())
    for clinic_id in clinic_ids:
        await wake(clinic_id)
    return clinic_ids


async def replay_dead_events(db, *, by: str, clinic_id: UUID | None = None, event_id: UUID | None = None) -> int:
    """Resend events from the dead queue, once their cause is fixed: they rejoin their clinic's
    line in their original order with a fresh set of retries. Recorded in the audit log."""
    if not by.strip():
        raise ValueError("Say who is resending (by)")
    query = select(InboundEvent).where(InboundEvent.processing_status == DEAD)
    if event_id is not None:
        query = query.where(InboundEvent.id == event_id)
    if clinic_id is not None:
        query = query.where(InboundEvent.clinic_id == clinic_id)
    events = (await db.scalars(query)).all()
    logs = AppointmentSyncLogService(db)
    for event in events:
        event.processing_status = "queued"
        event.attempt_count = 0
        event.next_attempt_at = None
        event.failure_reason = None
        event.processed_at = None
        record_audit(db, clinic_id=event.clinic_id, entity_type="inbound_event", entity_id=event.id,
                     action="replayed_from_dead_queue", source="tools", details={"by": by})
        await db.commit()
        sync_log = await _sync_log_for(db, event)
        if sync_log:
            await logs.mark_waiting(sync_log, reason=f"Resent by our team ({by})", failure_source=SyncFailureSource.NONE)
    for clinic_id_ in {event.clinic_id for event in events}:
        clinic = await db.get(RegisteredClinics, clinic_id_)
        if clinic is not None:
            await sync_alerts.resolve_dead_queue_if_empty(db, clinic)
            await wake(clinic_id_)
    return len(events)


# ------------------------------------------------------------------ the line

@dataclass
class Turn:
    handled: int = 0
    more_ready: bool = False            # stopped at the batch size with events still ready
    wake_at: datetime | None = None     # when the soonest waiting event becomes ready


async def work_through_clinic(clinic_id: UUID, *, batch_size: int = BATCH_SIZE) -> dict:
    async with worker_session() as db:
        handled = 0
        for _ in range(MAX_ROUNDS):
            try:
                async with clinic_sync_lock(db, clinic_id):
                    turn = await take_turn(db, clinic_id, batch_size=batch_size)
            except ClinicBusyError:
                # Another worker is on this clinic and will reach our event as well.
                return {"status": "busy", "handled": handled}
            handled += turn.handled
            if turn.more_ready:
                break
            # The lock is free again. An event that arrived while we held it found the
            # clinic busy and was left to us, so look once more before leaving.
            clinic = await _load_clinic(db, clinic_id)
            if clinic is None or await ready_event(db, clinic, _now()) is None:
                if turn.wake_at is not None:
                    await wake(clinic_id, at=turn.wake_at)
                return {"status": "done", "handled": handled}
        # More is ready: queue another job behind the other clinics' jobs.
        await wake(clinic_id)
        return {"status": "more waiting", "handled": handled}


async def take_turn(db, clinic_id: UUID, *, batch_size: int) -> Turn:
    """Process up to batch_size of the clinic's ready events, oldest first. The caller holds the lock."""
    turn = Turn()
    for _ in range(batch_size):
        clinic = await _load_clinic(db, clinic_id)
        if clinic is None:
            return turn
        at = _now()
        event = await ready_event(db, clinic, at)
        if event is None:
            turn.wake_at = await _soonest_ready(db, clinic, at)
            return turn
        await process_event(db, clinic, event)
        turn.handled += 1
    turn.more_ready = True
    return turn


async def ready_event(db, clinic: RegisteredClinics, at: datetime) -> InboundEvent | None:
    """The clinic's next event to process, or None: nothing ready, the line is paused, or
    the clinic is disabled (its events wait until it is enabled again)."""
    if clinic.is_disabled or opendental_paused_until(clinic, at):
        return None
    earlier = aliased(InboundEvent)
    behind_an_earlier_one = exists().where(
        earlier.clinic_id == InboundEvent.clinic_id,
        earlier.event_id == InboundEvent.event_id,  # the same CRM appointment
        earlier.processing_status.in_(HOLDS_LATER_CHANGES),
        tuple_(earlier.received_at, earlier.id) < tuple_(InboundEvent.received_at, InboundEvent.id),
    )
    return await db.scalar(
        select(InboundEvent)
        .where(InboundEvent.clinic_id == clinic.id, InboundEvent.processing_status.in_(WAITING),
               or_(InboundEvent.next_attempt_at.is_(None), InboundEvent.next_attempt_at <= at),
               ~behind_an_earlier_one)
        .order_by(InboundEvent.received_at, InboundEvent.id).limit(1)
        .execution_options(populate_existing=True)
    )


async def _soonest_ready(db, clinic: RegisteredClinics, at: datetime) -> datetime | None:
    if clinic.is_disabled:
        return None
    paused_until = opendental_paused_until(clinic, at)
    if paused_until:
        return paused_until
    soonest = await db.scalar(select(func.min(InboundEvent.next_attempt_at)).where(
        InboundEvent.clinic_id == clinic.id, InboundEvent.processing_status.in_(WAITING),
        InboundEvent.next_attempt_at > at))
    return as_utc(soonest) if soonest else None


async def _load_clinic(db, clinic_id: UUID) -> RegisteredClinics | None:
    # populate_existing: another worker or request may have changed it since we last looked.
    return await db.get(RegisteredClinics, clinic_id, populate_existing=True)


# ------------------------------------------------------------------ one event

async def process_event(db, clinic: RegisteredClinics, event: InboundEvent) -> str:
    """Take one event as far as it can go now. Returns its new status."""
    logs = AppointmentSyncLogService(db)
    sync_log = await _sync_log_for(db, event)
    event.processing_status = "processing"
    event.next_attempt_at = None
    await db.commit()
    if sync_log:
        await logs.mark_processing(sync_log)

    try:
        status = await _send_to_opendental(db, logs, clinic, event, sync_log)
    except OutcomeNotKnownYet as exc:
        await _reload_after_failure(db, clinic, event, sync_log)
        return await _wait(db, logs, event, sync_log, str(exc), until=_now() + LOOK_AGAIN_SOON)
    except ReconciliationRequired as exc:
        # Several matching records in OpenDental: a person picks one with tools.stuck_operations.
        # The event keeps later changes behind it, because a create may already exist.
        await _reload_after_failure(db, clinic, event, sync_log)
        return await _count_failure(db, logs, clinic, event, sync_log, status="waiting", audience=sync_alerts.INTERNAL,
                                    code="outcome_unclear", subject=f"event:{event.id}", alert_now=True,
                                    message=f"Check OpenDental with tools.stuck_operations: {exc}")
    except OPENDENTAL_DOWN as exc:
        await _reload_after_failure(db, clinic, event, sync_log)
        return await _opendental_down(db, logs, clinic, event, sync_log, exc)
    except CLINIC_SIDE as exc:
        await _reload_after_failure(db, clinic, event, sync_log)
        if clinic.is_disabled:
            # Disabled during this turn (OpenDental's client then refuses): not the clinic's
            # keys. The event waits, untouched, until the clinic is enabled again.
            return await _wait(db, logs, event, sync_log, "Clinic is disabled; kept until it is enabled again",
                               until=None, source=SyncFailureSource.CUSTOMER_CONFIGURATION)
        problem = _as_clinic_problem(exc)
        if problem.code == OPENDENTAL_CREDENTIALS and clinic.od_health_status != "auth_failed":
            mark_od_auth_failed(clinic, reason=problem.message)  # shown on the clinic list
            await db.commit()
        logger.warning("CRM event needs the clinic", extra={"clinic_id": str(clinic.id), "problem": problem.code})
        subject = "clinic" if problem.code == OPENDENTAL_CREDENTIALS else (problem.subject or f"event:{event.id}")
        return await _count_failure(db, logs, clinic, event, sync_log, status="needs_attention",
                                    audience=sync_alerts.CLINIC, code=problem.code, message=problem.message,
                                    subject=subject, alert_now=True)
    except Exception as exc:
        await _reload_after_failure(db, clinic, event, sync_log)
        logger.exception("CRM event failed", extra={"clinic_id": str(clinic.id), "event_id": event.event_id})
        if isinstance(exc, SQLAlchemyError):
            # Its text can include the SQL and its values; keep those out of what is stored.
            message = f"Database error ({type(exc).__name__})"
        else:
            message = str(exc) or type(exc).__name__
        return await _count_failure(db, logs, clinic, event, sync_log, status="retrying", audience=sync_alerts.INTERNAL,
                                    code="app_error", message=message, subject=type(exc).__name__, alert_now=False)
    await _after_success(db, clinic, event)
    return status


async def _send_to_opendental(db, logs: AppointmentSyncLogService, clinic: RegisteredClinics,
                              event: InboundEvent, sync_log: AppointmentSyncLog | None) -> str:
    payload = decode_json_secret(event.payload)
    if not isinstance(payload, dict):
        raise ValueError("Invalid event payload")
    contact_id = payload.get("contact_id", "")
    log_ctx = {"clinic_id": str(clinic.id), "event_id": payload.get("event_id"),
               "contact_ref": fingerprint_value(contact_id)}
    logger.info("CRM event started", extra=log_ctx)

    od = openDentalApi(clinic)
    patient = patient_model(
        FName=payload.get("first_name", ""), LName=payload.get("last_name", ""),
        Gender=payload.get("Gender"), Address=payload.get("Address"), Birthdate=payload.get("BirthDate"),
        WirelessPhone=payload.get("WirelessPhone"), Email=payload.get("Email"),
    )
    resolved = await PatientService(db, od, clinic.id).resolve_patnum(pat=patient, contact_id=contact_id)
    if resolved is None:
        raise ValueError("The contact could not be matched to a patient")
    pat_id, pat_num = resolved

    request = AppointmentRequest(
        date_str=payload.get("Date", ""), start_str=payload.get("start_time", ""),
        end_str=payload.get("end_time", ""), status=payload.get("status", ""),
        calendar_id=payload.get("calendar_id", ""), event_id=payload.get("event_id", ""),
        contact_id=contact_id, pop_up=payload.get("pop_up", ""), commslog=payload.get("commslog", ""),
        # The webhook requires "Note"; "Notes" is the older optional field.
        Note=payload.get("Note") or payload.get("Notes") or "",
        pat_Num=pat_num, clinic_timezone=clinic.clinic_timezone, pat_id=pat_id,
    )
    booking = await AppointmentService(db=db, od_client=od, clinic=clinic).book(
        request, sync_log_service=logs, sync_log=sync_log, received_at=event.received_at)
    if booking is None:
        raise ValueError("The booking returned no result")

    if booking.action == "superseded":
        logger.info("CRM event skipped; a newer change was already sent", extra=log_ctx)
        await _close(db, event, "superseded")
        if sync_log:
            await logs.mark_superseded(sync_log, reason="A newer change to this appointment was already sent to OpenDental")
        return "superseded"

    logger.info("Appointment synced", extra={**log_ctx, "apt_num": booking.apt_num, "action": booking.action})
    appointment_id = await db.scalar(select(Appointments.id).filter_by(clinic_id=clinic.id, event_id=request.event_id).limit(1))
    await _close(db, event, "processed")
    if sync_log:
        await logs.mark_success(sync_log, reason="Created in opendental Successfully", operation=booking.action,
                                appointment_id=appointment_id, pat_id=pat_id, apt_num=booking.apt_num)
    return "processed"


def _as_clinic_problem(exc: Exception) -> ClinicProblem:
    if isinstance(exc, ClinicProblem):
        return exc
    if isinstance(exc, OpenDentalAuthError):
        return ClinicProblem(OPENDENTAL_CREDENTIALS, "OpenDental rejected the clinic's API keys. "
                             "Update the OpenDental developer and customer keys.")
    if isinstance(exc, PatientMatchError):
        return ClinicProblem(PATIENT_MATCH, f"{exc} Check the patient in OpenDental and in the CRM.")
    return ClinicProblem(OPENDENTAL_REFUSED, "OpenDental refused the request. Check the clinic's chair "
                         "mapping and the appointment details in the CRM.")


# ------------------------------------------------------------------ outcomes

async def _after_success(db, clinic: RegisteredClinics, event: InboundEvent) -> None:
    """OpenDental took the event: close what it settles."""
    # OpenDental answered this clinic, so outage and credential alerts are over.
    await sync_alerts.resolve_codes(db, clinic, OPENDENTAL_ANSWERED)
    await sync_alerts.resolve_if_nothing_waits(db, clinic, event.alert_id)
    # Earlier changes to this appointment still retrying, or in the dead queue, are now out of date.
    logs = AppointmentSyncLogService(db)
    older = (await db.scalars(select(InboundEvent).where(
        InboundEvent.clinic_id == clinic.id, InboundEvent.event_id == event.event_id,
        InboundEvent.processing_status.in_(("retrying", "needs_attention", DEAD)),
        InboundEvent.received_at <= event.received_at, InboundEvent.id != event.id))).all()
    for stale in older:
        await _close(db, stale, "superseded", "Replaced by a newer change to this appointment")
        sync_log = await _sync_log_for(db, stale)
        if sync_log:
            await logs.mark_superseded(sync_log, reason="Replaced by a newer change to this appointment, which went through")
        await sync_alerts.resolve_if_nothing_waits(db, clinic, stale.alert_id)
    if older:
        await sync_alerts.resolve_dead_queue_if_empty(db, clinic)


async def _opendental_down(db, logs, clinic: RegisteredClinics, event: InboundEvent, sync_log, error) -> str:
    paused_until = mark_od_unreachable(clinic, reason=str(error))
    await db.commit()
    if clinic.dso_id:
        await invalidate_dso_clinic_list_cache(dso_id=clinic.dso_id)
    logger.warning("OpenDental is not answering; the clinic's line is paused",
                   extra={"clinic_id": str(clinic.id), "paused_until": paused_until.isoformat()})
    if unreachable_too_long(clinic, _now()):
        since = as_utc(clinic.od_health_changed_at)
        await sync_alerts.raise_alert(
            db, clinic, audience=sync_alerts.CLINIC, code=OPENDENTAL_UNREACHABLE, subject="clinic",
            message=f"OpenDental has not answered since {since:%d %b %H:%M} UTC. Check that the clinic's "
                    "OpenDental server and eConnector are running.")
    return await _wait(db, logs, event, sync_log, "Waiting for OpenDental to answer again", until=None)


async def _count_failure(db, logs, clinic: RegisteredClinics, event: InboundEvent, sync_log, *, status: str,
                         audience: str, code: str, message: str, subject: str, alert_now: bool) -> str:
    """A failed attempt that counts: retry on the RETRY_AFTER schedule, then the dead queue.

    alert_now: raise the alert at the first failure (clinic problems, outcomes a person must
    settle). Our own errors are only logged until they reach the dead queue."""
    event.attempt_count += 1
    if alert_now:
        alert = await sync_alerts.raise_alert(db, clinic, audience=audience, code=code, message=message, subject=subject)
        event.alert_id = alert.id
    if event.attempt_count > MAX_RETRIES:
        return await _dead(db, logs, clinic, event, sync_log, audience=audience, message=message)
    event.processing_status = status
    event.failure_reason = message
    event.next_attempt_at = _now() + RETRY_AFTER[event.attempt_count - 1]
    await db.commit()
    if sync_log:
        retries_left = MAX_RETRIES - event.attempt_count + 1
        if audience == sync_alerts.CLINIC:
            reason, source = f"Needs the clinic: {message}", SyncFailureSource.CUSTOMER_CONFIGURATION
        else:
            # The details are for our team; clinics see only that we are on it.
            reason, source = "Something went wrong on our side; retrying", SyncFailureSource.INTERNAL
        await logs.mark_failure(sync_log, reason=f"{reason} ({retries_left} retries left)", should_retry=True,
                                operation=None, failure_source=source)
    return status


async def _dead(db, logs, clinic: RegisteredClinics, event: InboundEvent, sync_log, *, audience: str,
                message: str) -> str:
    """Stop retrying: the event is kept in the dead queue and our team is alerted."""
    event.processing_status = DEAD
    event.failure_reason = f"Stopped after {MAX_RETRIES} retries: {message}"
    event.next_attempt_at = None
    event.processed_at = _now()
    await db.commit()
    alert = await sync_alerts.raise_alert(
        db, clinic, audience=sync_alerts.INTERNAL, code=sync_alerts.DEAD_QUEUE, subject="clinic",
        message=f"CRM changes stopped after {MAX_RETRIES} retries and wait in the dead queue. Fix the cause, then "
                f"resend them: python -m tools.sync_line replay --clinic {clinic.id} --by <your name>")
    if event.alert_id is None:
        event.alert_id = alert.id  # a clinic problem keeps its own alert open for the clinic instead
        await db.commit()
    logger.error("CRM event moved to the dead queue", extra={"clinic_id": str(clinic.id), "event_id": event.event_id,
                                                            "reason": message})
    if sync_log:
        if audience == sync_alerts.CLINIC:
            reason, source = f"Stopped after {MAX_RETRIES} retries. Needs the clinic: {message}", \
                SyncFailureSource.CUSTOMER_CONFIGURATION
        else:
            reason, source = f"Stopped after {MAX_RETRIES} retries; our team has been alerted", SyncFailureSource.INTERNAL
        await logs.mark_failure(sync_log, reason=reason, should_retry=False, operation=None, failure_source=source)
    return DEAD


async def _close(db, event: InboundEvent, status: str, reason: str | None = None) -> None:
    event.processing_status = status
    event.failure_reason = reason
    event.next_attempt_at = None
    event.processed_at = _now()
    await db.commit()


async def _wait(db, logs, event: InboundEvent, sync_log, reason: str, *, until: datetime | None,
                source: SyncFailureSource = SyncFailureSource.OPEN_DENTAL) -> str:
    # Not a failed attempt: attempt_count stays as it is. until=None: the clinic's pause decides.
    event.processing_status = "waiting"
    event.failure_reason = reason
    event.next_attempt_at = until
    await db.commit()
    if sync_log:
        await logs.mark_waiting(sync_log, reason=reason, failure_source=source)
    return "waiting"


async def _reload_after_failure(db, *rows) -> None:
    # A failed call may have rolled the session back, which expires every loaded row;
    # reading an expired row in an async session fails, so reload them first.
    await db.rollback()
    await reload_expired(db, *(row for row in rows if row is not None))


async def _sync_log_for(db, event: InboundEvent) -> AppointmentSyncLog | None:
    if event.sync_log_id:
        sync_log = await db.get(AppointmentSyncLog, event.sync_log_id)
        if sync_log:
            return sync_log
    # Events saved before inbound_events.sync_log_id existed.
    return await db.scalar(select(AppointmentSyncLog).filter_by(inbound_event_id=event.id).limit(1))
