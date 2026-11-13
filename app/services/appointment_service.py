from app.infrastructure.providers.opendental import openDentalApi, OpenDentalAuthError, OpenDentalRequestError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.models.entities import Appointments, RegisteredClinics, AppointmentSyncLog
from app.schemas.api import AppointmentRequest, Appointments_create, Appointments_update, create_commslogs, create_pop_ups
from datetime import datetime, timezone
from app.infrastructure.cache.operatory_cache import get_operatory_day_appointments_cached, set_operatory_day_appointments_cached, invalidate_operatory_day_cache
from app.infrastructure.cache.dso_clinic_page_cache import invalidate_dso_clinic_list_cache
from app.services.appointment_sync_log_helper import AppointmentSyncLogService
from app.services.clinic_health import mark_od_auth_failed, mark_od_health_ok
from dataclasses import dataclass 
import logging 
import asyncio
from typing import Any, Literal
from app.infrastructure.circuit_breaker import CircuitBreakerOpenError
from app.services.external_operations import ExternalOperations, OutcomeNeedsPerson, OutcomeNotKnownYet, ReconciliationRequired, stored_request
from app.services.reconciliation_checks import HAPPENED, NOT_DONE, WAITING, appointment_to_look_for, created_appointment_finding
from app.security.encryption import hash_lookup
from uuid import uuid5, NAMESPACE_URL
from app.infrastructure.providers.payloads import check_time_slot, fmt, opendental_get_operatory_status, opendental_pattern_time_build

logger = logging.getLogger(__name__)


class CustomerConfigurationSyncError(Exception):
    pass


class ClinicProblem(CustomerConfigurationSyncError):
    """Something only the clinic can fix; sending the event again cannot help until they do.

    code says what kind of problem it is, so alerts are grouped and explained; subject
    narrows it (a calendar, a patient), and events sharing code and subject share one alert.
    The message is shown to clinic staff and emailed, so it must never contain patient details.
    """

    def __init__(self, code: str, message: str, *, subject: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.subject = subject


# ClinicProblem codes
OPENDENTAL_CREDENTIALS = "opendental_credentials"   # affects every event: the line pauses
OPENDENTAL_UNREACHABLE = "opendental_unreachable"   # raised by the worker after an hour of outage
CHAIR_MAPPING = "chair_mapping"
NO_FREE_SLOT = "no_free_slot"
PATIENT_MATCH = "patient_match"
OPENDENTAL_REFUSED = "opendental_refused"


class OpenDentalSyncError(Exception):
    pass


class InternalSyncError(Exception):
    pass


BookingAction = Literal["created", "updated", "unchanged", "create", "update", "superseded"]

@dataclass
class AppointmentBookingResult:
    apt_num: int | None  # None only when "superseded" reaches a row without an OpenDental number
    action: BookingAction


def _as_utc(value: datetime) -> datetime:
    # SQLite (tests) gives back naive datetimes; PostgreSQL gives aware ones.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _older_than_applied(reserve: Appointments | None, received_at: datetime | None) -> bool:
    """True when a newer event has already been applied to this appointment."""
    if reserve is None or received_at is None or reserve.last_event_received_at is None:
        return False
    return _as_utc(received_at) < _as_utc(reserve.last_event_received_at)


def _note_to_send(reserve: Appointments | None, req: AppointmentRequest) -> str | None:
    """The CRM's note when it differs from the one last written to OpenDental, else None.
    OpenDental replaces the whole note with it. An empty note is never sent, so a workflow
    that leaves the note out cannot wipe the note in OpenDental."""
    note = (req.Note or "").strip()
    if not note or reserve is None or reserve.note_hash == hash_lookup(note):
        return None
    return note


def _record_applied_event(reserve: Appointments, received_at: datetime | None) -> None:
    # The caller commits this with the appointment change itself, so the two never disagree.
    if received_at is not None and not _older_than_applied(reserve, received_at):
        reserve.last_event_received_at = received_at

class AppointmentService():
    def __init__(self, db: AsyncSession, clinic: RegisteredClinics, od_client: openDentalApi) : 
        self.od = od_client
        self.db = db
        self.clinic = clinic
        self.operations = ExternalOperations(db)
        # book_into_operatory queries operatories concurrently (asyncio.gather), and each
        # result commits clinic health. An AsyncSession must not be used by two tasks at
        # once, so session work in _run_od_call takes turns; the HTTP calls still overlap.
        self._session_lock = asyncio.Lock()

    def operation_key(self, req, action):
        return f"{action}:{self.clinic.id}:{hash_lookup(req.event_id)}"

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _apply_status_transition(self, row: Appointments, new_status: str):
        if row.status == new_status:
            return

        row.previous_status = row.status
        row.status = new_status
        row.status_changed_at = self._utcnow()

    def _format_od_datetime(self, value: datetime) -> str:
        return value.strftime(fmt)
    
    async def _run_od_call(self, od_call, *, operation: str ):
        try:
            result = await od_call
        except CircuitBreakerOpenError:
            raise
        except OpenDentalRequestError as exc:
            raise ClinicProblem(OPENDENTAL_REFUSED, "OpenDental refused the request. Check the clinic's chair "
                                "mapping and the appointment details in the CRM.") from exc
           
        except OpenDentalAuthError as exc:
            async with self._session_lock:
                mark_od_auth_failed(self.clinic, reason=str(exc))
                await self.db.commit()

            if self.clinic.dso_id:
                await invalidate_dso_clinic_list_cache(dso_id=self.clinic.dso_id)
                
            logger.warning(
            "OpenDental auth failed",
            extra={
                "clinic_id": str(self.clinic.id),
                "clinic_name": self.clinic.clinic_name,
                "operation": operation,
                "reason": str(exc),
            },
            )
            raise ClinicProblem(OPENDENTAL_CREDENTIALS, "OpenDental rejected the clinic's API keys. "
                                "Update the OpenDental developer and customer keys.") from exc
        
        except (TimeoutError, ConnectionError) as exc:
            logger.warning(
            "OpenDental connection failure",
            extra={
                "clinic_id": str(self.clinic.id),
                "clinic_name": self.clinic.clinic_name,
                "operation": operation,
                "reason": str(exc),
            },
        )
            raise OpenDentalSyncError("Open Dental is unavailable") from exc
        
        except Exception as exc:
            logger.exception(
            "Unexpected OpenDental processing error",
            extra={
                "clinic_id": str(self.clinic.id),
                "clinic_name": self.clinic.clinic_name,
                "operation": operation,
            },
        )
            raise InternalSyncError("Internal processing error") from exc
            
        async with self._session_lock:
            mark_od_health_ok(self.clinic)
            await self.db.commit()

        if self.clinic.dso_id:
            await invalidate_dso_clinic_list_cache(dso_id=self.clinic.dso_id)

        logger.info(
        "OpenDental operation succeeded",
        extra={
            "clinic_id": str(self.clinic.id),
            "clinic_name": self.clinic.clinic_name,
            "operation": operation,
        },
        )
        return result

    
    async def get_operatory_appointments_cached(
        self, 
        *,
        operatory: int,
        date_start: str,
        date_end: str,
        fresh: bool = False,
        ) -> list[dict[str, Any]]:

        cached = None if fresh else await get_operatory_day_appointments_cached(
            clinic_id=self.clinic.id,
            operatory=operatory,
            date_start=date_start,
            date_end=date_end
            )
        
        if cached is not None and not fresh:
            return cached 
        
        appointments = await self._run_od_call(
            self.od.get_appointments_in_operatory(
                operatory,
                date_start,
                date_end,
            ), 
            operation= "get_operatory_appointments"
        )


        await set_operatory_day_appointments_cached(
            clinic_id=self.clinic.id,
            operatory=operatory,
            date_start=date_start,
            date_end=date_end,
            appointments=appointments,
        )
        return appointments
        


    async def book(self, req: AppointmentRequest, * , sync_log_service: AppointmentSyncLogService | None = None,
        sync_log: AppointmentSyncLog | None = None, received_at: datetime | None = None) -> AppointmentBookingResult | None:
        """received_at: when our server got this event. Pass it so an older event never
        overwrites a newer one; None skips that check."""
        if self.clinic.is_disabled:
            raise CustomerConfigurationSyncError("Clinic is disabled")
        start_dt, end_dt, pattern = self.build_time(req)
        start_text = self._format_od_datetime(start_dt)
        end_text = self._format_od_datetime(end_dt)

        reserve = await self.book_reserve(req, start_text=start_text, end_text=end_text)
        if _older_than_applied(reserve, received_at):
            # The appointment itself is left alone: OpenDental already shows a newer state.
            # A comm log or pop-up is not state, so it still goes if this appointment never had one.
            if reserve.AptNum:
                await self.finish_side_effects(reserve, req)
            return AppointmentBookingResult(apt_num=reserve.AptNum, action="superseded")
        adopted = False
        if reserve and not reserve.AptNum:
            await self._settle_earlier_create(req)
            adopted = await self._adopt_confirmed_create(req, reserve)
        existing_aptnum = reserve.AptNum if (reserve and reserve.AptNum ) else None
        note = _note_to_send(reserve, req)

        if reserve and existing_aptnum:
            change = bool(
            reserve.status != req.status
            or reserve.date != req.date_str
            or reserve.start_time.strip() != start_text
            or reserve.end_time.strip() != end_text
            or reserve.calendar_id != req.calendar_id
            )

            if not change:
                if note is not None:
                    # Only the note changed. Send just the note: no chair check, so a note
                    # change is never refused because the chair has since filled up.
                    await self._run_od_call(self.od.update_appointment_note(Aptnum=existing_aptnum, note=note),
                                            operation="update_appointment_note")
                    reserve.note_hash = hash_lookup(note)
                if sync_log_service and sync_log:
                    await sync_log_service.mark_operation(sync_log, operation="unchanged" if note is None else "update")
                _record_applied_event(reserve, received_at)
                await self.db.commit()
                await self.finish_side_effects(reserve, req)
                return AppointmentBookingResult(
                    apt_num=int(existing_aptnum),
                    action="created" if adopted else ("unchanged" if note is None else "updated"),
                )
            
        intended_operation: BookingAction = "update" if existing_aptnum else "create"
        if sync_log_service and sync_log:
            await sync_log_service.mark_operation(sync_log, operation=intended_operation)

        operatories = self.get_operatories(req)

        if not operatories:
            raise ClinicProblem(CHAIR_MAPPING, f"No chair is mapped to CRM calendar {req.calendar_id} for "
                                f"'{req.status}' appointments. Add it to the clinic's chair mapping.",
                                subject=f"{req.calendar_id}:{req.status}")

        booking = await self.book_into_operatory( req, operatories = operatories , start_dt = start_dt , end_dt = end_dt, pattern = pattern, AptNum= existing_aptnum, note=note)

        if not booking:
            raise ClinicProblem(NO_FREE_SLOT, f"No mapped chair is free on {req.date_str} from {req.start_str} "
                                f"to {req.end_str}. Free a chair in OpenDental or move the appointment in the CRM.")
        
        logger.info(f"Appointment is being booked for {booking.apt_num} in {operatories} for clinic {self.clinic.clinic_name}")
        if reserve:
            reserve.AptNum = booking.apt_num
            self._apply_status_transition(reserve, req.status)
            reserve.date = req.date_str   # type: ignore
            reserve.start_time = start_text   # type: ignore
            reserve.end_time = end_text    # type: ignore
            reserve.calendar_id = req.calendar_id
            _record_applied_event(reserve, received_at)
            sent_note = (req.Note or "").strip() if booking.action == "created" else note
            if sent_note:
                reserve.note_hash = hash_lookup(sent_note)
            if booking.action == "created":
                await self.operations.mark_applied(self.operation_key(req, "appointment_create"))
            await self.db.commit()

        if reserve:
            await self.finish_side_effects(reserve, req)
        return booking

    async def _settle_earlier_create(self, req: AppointmentRequest) -> None:
        """If an earlier attempt to create this appointment timed out, ask OpenDental's own
        records what it did before doing anything else: creating again could double-book.

        Found: recorded as created, and the caller reuses its number. Missing after
        OPENDENTAL_NOT_DONE_AFTER: recorded as not done, so it is created afresh. Otherwise
        raises OutcomeNotKnownYet (look again shortly) or OutcomeNeedsPerson.
        """
        operation = await self.operations.get(self.operation_key(req, "appointment_create"))
        if not self.operations.unknown(operation):
            return
        request = stored_request(operation)
        lookup = appointment_to_look_for(request)
        if lookup is None:
            raise OutcomeNeedsPerson("An earlier booking attempt timed out before its details were kept")
        chair, day = lookup
        appointments = await self._run_od_call(self.od.get_appointments_in_operatory(chair, day, day),
                                               operation="check_earlier_booking")
        age = self._utcnow() - _as_utc(operation.created_at)
        finding = created_appointment_finding(appointments or [], request, age)
        if finding.outcome == HAPPENED:
            await self.operations.settle_from_provider_read(
                operation, result={"AptNum": int(finding.fields["AptNum"])}, evidence=finding.evidence)
        elif finding.outcome == NOT_DONE:
            await self.operations.settle_from_provider_read(operation, result=None, evidence=finding.evidence)
        elif finding.outcome == WAITING:
            raise OutcomeNotKnownYet(f"An earlier booking attempt timed out. {finding.evidence}")
        else:
            raise OutcomeNeedsPerson(finding.evidence)

    async def _adopt_confirmed_create(self, req: AppointmentRequest, reserve: Appointments) -> bool:
        # An earlier attempt created this appointment in OpenDental, and the journal knows
        # its number (from OpenDental's answer, a person, or the automatic check), but the
        # row never got it. Without this the next attempt books again: our own appointment
        # now fills the slot, so it picks another chair and the journal refuses the request.
        key = self.operation_key(req, "appointment_create")
        result = await self.operations.confirmed_result(key)
        if not result or not result.get("AptNum"):
            return False
        reserve.AptNum = int(result["AptNum"])
        await self.operations.mark_applied(key)
        await self.db.commit()
        return True

    async def finish_side_effects(self, reserve: Appointments, req: AppointmentRequest) -> None:
        if reserve and not reserve.commslog_done and req.commslog:   # type: ignore
            if await self._side_effect_sent(self.handle_commslog, req, "commslog"):
                reserve.commslog_done = True
                await self.operations.mark_applied(self.operation_key(req, "commslog"))
                await self.db.commit()

        if  reserve and not reserve.popups_done and req.pop_up:   # type: ignore
            if await self._side_effect_sent(self.handle_popups, req, "popup"):
                reserve.popups_done = True
                await self.operations.mark_applied(self.operation_key(req, "popup"))
                await self.db.commit()

    async def _side_effect_sent(self, send, req: AppointmentRequest, action: str) -> bool:
        try:
            await send(req)
            return True
        except ReconciliationRequired:
            # An earlier attempt to add this note timed out; adding it again could duplicate
            # it. It is listed in tools.stuck_operations for a person. The appointment itself
            # is already right, so the event carries on rather than waiting on a note.
            logger.warning("Skipped an OpenDental %s whose earlier attempt has an unknown outcome", action,
                           extra={"clinic_id": str(self.clinic.id)})
            return False


    async def book_into_operatory(
        self,
        req: AppointmentRequest,
        operatories: list[int],
        start_dt: datetime,
        end_dt: datetime,
        pattern: str,
        AptNum: int | None,
        note: str | None = None,
    ) -> AppointmentBookingResult | None:
        #Create date pattern for date time start and date time end
        date_start = start_dt.strftime("%Y-%m-%d")
        date_end = end_dt.strftime("%Y-%m-%d")

        results = await asyncio.gather(
            *[
                self.get_operatory_appointments_cached(
                    operatory= op,
                    date_start=date_start,
                    date_end=date_end,
                    fresh=True,
                )
                for op in operatories
            ]
        )


        for op, existing in zip(operatories, results):
            if not check_time_slot(existing, start_dt, end_dt, exclude_apt_num=AptNum):
                continue

            if AptNum: 
                logger.info(f"Updated Appointment for Aptnum {AptNum} in  Op  {op}")
                await self.update_appointment( 
                    pattern = pattern, 
                    req = req ,
                    op = op,  
                    start_dt = start_dt,
                    AptNum= AptNum,
                    note = note,
                    )
                
                await invalidate_operatory_day_cache(
                    clinic_id=self.clinic.id,
                    operatory=op,
                    date_start=date_start,
                    date_end=date_end,
                )
                
                return AppointmentBookingResult(apt_num = int(AptNum), action = "updated")
            
            logger.info(f"creating  Appointment for Aptnum {AptNum} in  Op  {op}")
            created = await self.create_appointment(
                    pattern = pattern,
                    op = op,
                    req = req,  
                    start_dt = start_dt 
                )
            
            created_aptnum = created.get("AptNum")
            if created_aptnum is None:
                return None
            
            await invalidate_operatory_day_cache(
                clinic_id=self.clinic.id,
                operatory=op,
                date_start=date_start,
                date_end=date_end,
            )

            return AppointmentBookingResult(
                apt_num=int(created_aptnum),
                action="created",
            )

        return None 
    


    async def book_reserve(self, req: AppointmentRequest, start_text: str, end_text: str) -> Appointments | None:
        if  not req.event_id:
            return None 

        row = await self.db.scalar(select(Appointments).filter_by(clinic_id = self.clinic.id , event_id = req.event_id).limit(1))
        if row:
            return row 
        
        row = Appointments(
        clinic_id=self.clinic.id,
        event_id=req.event_id,
        status=req.status,
        previous_status=None,
        status_changed_at=self._utcnow(),
        start_time=start_text,
        end_time=end_text,
        date=req.date_str,
        calendar_id=req.calendar_id,
        pat_id=req.pat_id,          
        AptNum=None,               
         )
        
        try:
            self.db.add(row)
            await self.db.commit()
            await self.db.refresh(row)
            return row 
        
        except IntegrityError:
            await self.db.rollback()
            return (
            await self.db.scalar(select(Appointments)
            .filter_by(clinic_id=self.clinic.id, event_id=req.event_id).limit(1))
        )

        except SQLAlchemyError:
        # Anything else is a real DB issue — stop
            await self.db.rollback()
            raise

    async def create_appointment(
        self,
        req: AppointmentRequest,
        pattern: str,
        op: int,
        start_dt: datetime,
    ) -> dict[str, Any]:
        appointment = Appointments_create(
            PatNum= req.pat_Num,
            Pattern = pattern,
            AptDateTime=self._format_od_datetime(start_dt),
            Op = str(op),
            Note = req.Note,
            AptStatus = req.status
        )
        async def create():
            result = await self._run_od_call(self.od.create_appointments(appointment_data=appointment), operation="create_appointment")
            if not isinstance(result, dict) or not result.get("AptNum"):
                raise ValueError("Appointment creation requires reconciliation")
            return {"AptNum": int(result["AptNum"])}
        return await self.operations.execute(key=self.operation_key(req, "appointment_create"),
            resource_id=uuid5(NAMESPACE_URL, f"{self.clinic.id}:{req.event_id}"), action="appointment_create",
            request=appointment.model_dump(mode="json"), call=create)
    
    async def update_appointment(
        self,
        req: AppointmentRequest,
        pattern: str,
        AptNum: int,
        op: int,
        start_dt: datetime,
        note: str | None = None,
    ) -> dict[str, Any]:
        appointment = Appointments_update(
            Pattern= pattern,
            AptDateTime=self._format_od_datetime(start_dt),
            Op = str(op),
            AptStatus= req.status,
            Note = note,  # None: leave OpenDental's note as it is
        )

        return await self._run_od_call(
        self.od.update_appointment(Aptnum=AptNum, appointment_data=appointment),
        operation="update_appointment",
        )

    
    async def handle_commslog(self, req : AppointmentRequest):
        if not req.commslog:
            return 
        
        logs = create_commslogs(
            commlogs = req.commslog,
            PatNum = req.pat_Num 
        )
        async def create():
            await self._run_od_call(self.od.create_commslog(comms_logs=logs), operation="create_commslog")
            return {"created": True}
        await self.operations.execute(key=self.operation_key(req, "commslog"),
            resource_id=uuid5(NAMESPACE_URL, f"{self.clinic.id}:{req.event_id}"), action="commslog",
            request=logs.model_dump(), call=create)
    

    
    async def handle_popups(self, req: AppointmentRequest):
        if not req.pop_up:
            return 
        
        pops = create_pop_ups(
            pop_ups = req.pop_up,
            PatNum = req.pat_Num
        )

        async def create():
            await self._run_od_call(self.od.create_pops(pops=pops), operation="create_pop_ups")
            return {"created": True}
        await self.operations.execute(key=self.operation_key(req, "popup"),
            resource_id=uuid5(NAMESPACE_URL, f"{self.clinic.id}:{req.event_id}"), action="popup",
            request=pops.model_dump(), call=create)


    def get_operatories(self, req: AppointmentRequest) -> list[int]:
        return opendental_get_operatory_status(self.clinic, req.status, req.calendar_id) or []
    

    def build_time(self, req: AppointmentRequest) -> tuple[datetime, datetime, str]:
        return opendental_pattern_time_build(req.date_str, req.start_str, req.end_str, req.clinic_timezone)
    

