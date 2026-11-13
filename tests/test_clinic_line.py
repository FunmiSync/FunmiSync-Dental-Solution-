"""The per-clinic line of CRM events (SQLite, a fake lock and a fake OpenDental only).

The fake OpenDental behaves like the real one where it matters: it remembers the
patients and appointments it creates, it can be down or reject the clinic's keys, and
it can create an appointment and then lose the reply, which is what a timed-out booking is.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment, guarded_local_services
configure_test_environment()

from sqlalchemy import func, select

from local_database import close_local_session, local_session
from app.infrastructure.clinic_lock import ClinicBusyError
from app.infrastructure.providers.opendental import OpenDentalAuthError, OpenDentalRequestError, OpenDentalUnavailableError
from app.infrastructure.workers import appointments as worker
from app.models.entities import (
    AppointmentSyncLog, Appointments, ExternalOperation, InboundEvent, RegisteredClinics, RoleAssignment, RoleType,
    ScopeType, SyncAlert, SyncDirection, SyncFailureSource, SyncStatus, UserNotification, Users,
)
from app.security.encryption import encrypt_json_secret, encrypt_secret
from app.services import clinic_health, notification_service, sync_alerts
from app.services.appointment_sync_log_helper import AppointmentSyncLogService, SyncLogInput
from app.services.errors import NotFound

_guard = None


def setUpModule():
    global _guard
    _guard = guarded_local_services()
    _guard.__enter__()


def tearDownModule():
    _guard.__exit__(None, None, None)


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class FakeOpenDental:
    def __init__(self):
        self.patients = {}
        self.chairs = {1: [], 2: []}
        self.next_apt_num = 500
        self.down = False
        self.keys_rejected = False
        self.lose_reply_to_next_create = False
        self.refuse_last_names = set()
        self.break_last_names = set()
        self.creates = []
        self.updates = []
        self.notes = []

    def _answer(self):
        if self.down:
            raise OpenDentalUnavailableError("OpenDental connection failed")
        if self.keys_rejected:
            raise OpenDentalAuthError("OpenDental rejected clinic credentials")

    async def search_patients(self, last_name, date_of_birth):
        self._answer()
        if last_name in self.refuse_last_names:
            raise OpenDentalRequestError("OpenDental rejected the request")
        if last_name in self.break_last_names:
            raise RuntimeError("unexpected reply")
        return [p for p in self.patients.values() if p["LName"] == last_name and p["Birthdate"] == date_of_birth]

    async def create_patient(self, patient_data):
        self._answer()
        pat_num = 100 + len(self.patients) + 1
        self.patients[pat_num] = {"PatNum": pat_num, "FName": patient_data.FName, "LName": patient_data.LName,
                                  "Birthdate": patient_data.Birthdate}
        return {"PatNum": pat_num}

    async def get_appointments_in_operatory(self, op, start, end):
        self._answer()
        return [a for a in self.chairs[int(op)] if a["AptDateTime"][:10] == start]

    async def create_appointments(self, appointment_data):
        self._answer()
        appointment = {"AptNum": self.next_apt_num, "PatNum": appointment_data.PatNum, "Note": appointment_data.Note,
                       "AptDateTime": appointment_data.AptDateTime, "Pattern": appointment_data.Pattern}
        self.next_apt_num += 1
        self.chairs[int(appointment_data.Op)].append(appointment)
        self.creates.append(appointment)
        if self.lose_reply_to_next_create:
            self.lose_reply_to_next_create = False
            raise TimeoutError("no answer")  # created, but our side never heard back
        return {"AptNum": appointment["AptNum"]}

    async def update_appointment(self, Aptnum, appointment_data):
        self._answer()
        for chair in self.chairs.values():
            for appointment in list(chair):
                if appointment["AptNum"] == Aptnum:
                    chair.remove(appointment)
        self.chairs[int(appointment_data.Op)].append({"AptNum": Aptnum, "PatNum": None, "Pattern": appointment_data.Pattern,
                                                      "AptDateTime": appointment_data.AptDateTime})
        self.updates.append((Aptnum, appointment_data))
        return {}

    async def update_appointment_note(self, Aptnum, note):
        self._answer()
        self.notes.append((Aptnum, note))
        return {}

    async def create_commslog(self, comms_logs):
        self._answer()

    async def create_pops(self, pops):
        self._answer()

    def occupy(self, chair, start):
        """Someone else's appointment in that chair on 2026-12-01."""
        self.chairs[chair].append({"AptNum": 9000 + len(self.chairs[chair]), "PatNum": 1, "Pattern": "XXXXXX",
                                   "AptDateTime": f"2026-12-01 {start}:00"})


class FakeLocks:
    """Stands in for the PostgreSQL advisory lock: one holder per clinic."""

    def __init__(self):
        self.held = set()
        self.before_release = None  # runs once, while the lock is still held

    def __call__(self, db, clinic_id):
        return self._hold(clinic_id)

    @asynccontextmanager
    async def _hold(self, clinic_id):
        if clinic_id in self.held:
            raise ClinicBusyError("Clinic synchronization is already processing another event")
        self.held.add(clinic_id)
        try:
            yield
        finally:
            hook, self.before_release = self.before_release, None
            if hook:
                await hook()
            self.held.discard(clinic_id)


class ClinicLineTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()
        self.od = FakeOpenDental()
        self.locks = FakeLocks()
        self.wake = AsyncMock()
        self.email = AsyncMock()

        @asynccontextmanager
        async def session():
            yield self.db

        self.patches = [
            patch.object(worker, "worker_session", session),
            patch.object(worker, "clinic_sync_lock", self.locks),
            patch.object(worker, "wake", self.wake),
            patch.object(worker, "openDentalApi", lambda clinic: self.od),
            patch.object(sync_alerts, "send_email", self.email),
            patch("app.services.appointment_sync_log_helper.publish_sync_log_changed", AsyncMock()),
        ] + [patch(f"app.services.appointment_service.{name}", AsyncMock(return_value=None)) for name in (
            "get_operatory_day_appointments_cached", "set_operatory_day_appointments_cached",
            "invalidate_operatory_day_cache")]
        for active in self.patches:
            active.start()

        self.owner = await self.user("owner")
        self.clinic = RegisteredClinics(
            id=uuid4(), crm_type="ghl", clinic_name="Sunrise", clinic_number=1, clinic_timezone="UTC",
            od_developer_key=encrypt_secret("key"), od_customer_key=encrypt_secret("key"),
            crm_api_key=encrypt_secret("key"), location_id="location", calendar_id=encrypt_secret("calendar"),
            owner_id=self.owner.id,
            operatory_calendar_map={"Scheduled": [{"calendar_id": "calendar", "operatories": [1, 2]}]})
        self.db.add(self.clinic)
        await self.db.commit()
        self.arrivals = now() - timedelta(minutes=30)

    async def asyncTearDown(self):
        for active in reversed(self.patches):
            active.stop()
        await close_local_session(self.db, self.engine)

    async def user(self, name, role=None):
        user = Users(id=uuid4(), username=name, email=f"{name}@clinic.example", password="x")
        self.db.add(user)
        await self.db.commit()
        if role is not None:
            self.db.add(RoleAssignment(id=uuid4(), user_id=user.id, scope_type=ScopeType.CLINIC, role=role,
                                       clinic_id=self.clinic.id, is_active=True))
            await self.db.commit()
        return user

    async def event(self, first_name, last_name, start, *, appointment="", end=None, received_at=None,
                    note="", calendar="calendar", commslog=""):
        """A CRM event, saved the way the webhook saves it."""
        self.arrivals += timedelta(seconds=10)
        payload = {
            "event_id": appointment or f"appointment-{last_name}", "contact_id": f"contact-{last_name}",
            "first_name": first_name, "last_name": last_name, "BirthDate": "1990-01-01", "Gender": "Male",
            "Date": "2026-12-01", "start_time": start, "end_time": end or start.replace(":00", ":30"),
            "status": "Scheduled", "calendar_id": calendar, "commslog": commslog, "pop_up": "", "Note": note,
            "Notes": None, "WirelessPhone": "000", "Email": "patient@example.com",
        }
        sync_log = await AppointmentSyncLogService(self.db).get_or_create_sync_log(SyncLogInput(
            clinic_id=self.clinic.id, inbound_event_id=None, pat_id=None, appointment_id=None,
            contact_id=payload["contact_id"], event_id=payload["event_id"], apt_num=None,
            patient_name=f"{first_name} {last_name}", date_str=payload["Date"], start_str=start,
            end_str=payload["end_time"], appointment_status="Scheduled", direction=SyncDirection.CRM_TO_OD,
            payload=payload))
        row = InboundEvent(clinic_id=self.clinic.id, crm_type="ghl", event_id=payload["event_id"],
                           contact_id=encrypt_secret(payload["contact_id"]), payload=encrypt_json_secret(payload),
                           processing_status="queued", received_at=received_at or self.arrivals, sync_log_id=sync_log.id)
        self.db.add(row)
        await self.db.commit()
        return row

    async def run_line(self, **options):
        return await worker.work_through_clinic(self.clinic.id, **options)

    async def reload(self, row):
        await self.db.refresh(row)
        return row

    async def fresh_clinic(self):
        return await self.db.get(RegisteredClinics, self.clinic.id, populate_existing=True)

    async def sync_log(self, event):
        return await self.db.get(AppointmentSyncLog, event.sync_log_id, populate_existing=True)

    async def alerts(self):
        return (await self.db.scalars(select(SyncAlert).execution_options(populate_existing=True))).all()

    async def notifications(self):
        return (await self.db.scalars(select(UserNotification))).all()

    async def due_now(self, *rows):
        """Let time pass: every wait is over."""
        for row in rows:
            row.next_attempt_at = now() - timedelta(seconds=1)
        clinic = await self.fresh_clinic()
        if clinic.sync_paused_until:
            clinic.sync_paused_until = now() - timedelta(seconds=1)
        await self.db.commit()


class OrderTests(ClinicLineTestCase):
    async def test_events_go_to_opendental_in_the_order_they_arrived(self):
        john = await self.event("John", "Ade", "09:00")
        mary = await self.event("Mary", "Bello", "10:00")

        result = await self.run_line()

        self.assertEqual(result, {"status": "done", "handled": 2})
        self.assertEqual([a["AptDateTime"] for a in self.od.creates], ["2026-12-01 09:00:00", "2026-12-01 10:00:00"])
        for event in (john, mary):
            self.assertEqual((await self.reload(event)).processing_status, "processed")
            self.assertEqual((await self.sync_log(event)).sync_status, SyncStatus.PROCESSED)

    async def test_a_busy_clinic_is_left_to_the_worker_already_on_it(self):
        john = await self.event("John", "Ade", "09:00")
        self.locks.held.add(self.clinic.id)

        self.assertEqual(await self.run_line(), {"status": "busy", "handled": 0})

        john = await self.reload(john)
        self.assertEqual((john.processing_status, john.attempt_count), ("queued", 0))  # no retry used up
        self.assertEqual(self.od.creates, [])

    async def test_an_event_that_arrives_as_the_worker_finishes_is_not_stranded(self):
        await self.event("John", "Ade", "09:00")
        arrived = {}

        async def webhook_while_lock_is_held():
            # Its own job would find the clinic busy and leave; only "look once more" saves it.
            arrived["mary"] = await self.event("Mary", "Bello", "10:00")
        self.locks.before_release = webhook_while_lock_is_held

        result = await self.run_line()

        self.assertEqual(result["handled"], 2)
        self.assertEqual((await self.reload(arrived["mary"])).processing_status, "processed")

    async def test_a_full_turn_hands_the_worker_to_other_clinics(self):
        events = [await self.event(name, name, f"{hour}:00") for name, hour in (("Ada", 9), ("Ben", 10), ("Cy", 11))]

        result = await self.run_line(batch_size=2)

        self.assertEqual(result, {"status": "more waiting", "handled": 2})
        self.wake.assert_awaited_once_with(self.clinic.id)  # back of the queue, behind other clinics
        self.assertEqual((await self.reload(events[2])).processing_status, "queued")

    def test_an_older_job_from_before_the_line_just_wakes_the_clinic(self):
        # Jobs already in Redis name process_crm_load_job with the old arguments.
        with patch.object(worker, "work_through_clinic_job", return_value="done") as line:
            self.assertEqual(worker.process_crm_load_job(self.clinic.id, "ghl", {"encrypted_payload": "old"}, uuid4()), "done")
        line.assert_called_once_with(self.clinic.id)

    async def test_old_events_are_still_sent_however_old(self):
        april = await self.event("John", "Ade", "09:00", received_at=now() - timedelta(days=200))
        await self.run_line()
        self.assertEqual((await self.reload(april)).processing_status, "processed")
        self.assertEqual(len(self.od.creates), 1)

    async def test_a_disabled_clinics_events_wait_until_it_is_enabled(self):
        john = await self.event("John", "Ade", "09:00")
        clinic = await self.fresh_clinic()
        clinic.is_disabled = True
        await self.db.commit()

        self.assertEqual((await self.run_line())["handled"], 0)
        self.assertEqual((await self.reload(john)).processing_status, "queued")  # kept, untouched
        self.assertEqual(self.od.patients, {})

        clinic.is_disabled = False
        await self.db.commit()
        await self.run_line()
        self.assertEqual((await self.reload(john)).processing_status, "processed")

    async def test_an_event_left_processing_by_a_dead_worker_is_picked_up(self):
        john = await self.event("John", "Ade", "09:00")
        john.processing_status = "processing"  # its worker died mid-event; nobody holds the lock now
        await self.db.commit()
        await self.run_line()
        self.assertEqual((await self.reload(john)).processing_status, "processed")


class OpenDentalDownTests(ClinicLineTestCase):
    async def test_an_outage_pauses_the_line_without_using_retries(self):
        john = await self.event("John", "Ade", "09:00")
        mary = await self.event("Mary", "Bello", "10:00")
        self.od.down = True

        result = await self.run_line()

        self.assertEqual(result["handled"], 1)  # John's attempt found the outage; Mary was not tried
        john, mary = await self.reload(john), await self.reload(mary)
        self.assertEqual((john.processing_status, john.attempt_count), ("waiting", 0))
        self.assertEqual(mary.processing_status, "queued")
        clinic = await self.fresh_clinic()
        self.assertEqual(clinic.od_health_status, "unreachable")
        paused_until = utc(clinic.sync_paused_until)
        self.assertAlmostEqual((paused_until - now()).total_seconds(), 120, delta=5)
        self.wake.assert_awaited_once_with(self.clinic.id, at=paused_until)
        log = await self.sync_log(john)
        self.assertEqual((log.sync_status, log.is_billable), (SyncStatus.QUEUED, False))
        self.assertEqual(log.reason, "Waiting for OpenDental to answer again")
        self.assertEqual(await self.alerts(), [])  # a short outage alerts nobody

    async def test_the_line_carries_on_in_order_once_opendental_answers(self):
        john = await self.event("John", "Ade", "09:00")
        mary = await self.event("Mary", "Bello", "10:00")
        self.od.down = True
        await self.run_line()

        self.od.down = False
        await self.due_now()
        await self.run_line()

        self.assertEqual([a["AptDateTime"][11:16] for a in self.od.creates], ["09:00", "10:00"])
        self.assertEqual([(await self.reload(e)).processing_status for e in (john, mary)], ["processed", "processed"])
        clinic = await self.fresh_clinic()
        self.assertEqual((clinic.od_health_status, clinic.sync_paused_until), ("ok", None))

    async def test_a_paused_line_does_not_call_opendental_before_its_time(self):
        await self.event("John", "Ade", "09:00")
        self.od.down = True
        await self.run_line()
        self.od.down = False  # back, but the pause has not ended yet

        self.assertEqual((await self.run_line())["handled"], 0)
        self.assertEqual(self.od.creates, [])

    async def test_an_outage_over_an_hour_alerts_the_clinic_and_closes_when_it_ends(self):
        await self.user("manager", RoleType.MANAGER)
        john = await self.event("John", "Ade", "09:00")
        clinic = await self.fresh_clinic()
        clinic.od_health_status, clinic.od_health_changed_at = "unreachable", now() - timedelta(hours=2)
        await self.db.commit()
        self.od.down = True

        await self.run_line()

        [alert] = await self.alerts()
        self.assertEqual((alert.audience, alert.code), ("clinic", "opendental_unreachable"))
        self.assertIn("OpenDental has not answered since", alert.message)
        self.email.assert_awaited_once()

        self.od.down = False
        await self.due_now()
        await self.run_line()
        self.assertEqual((await self.reload(john)).processing_status, "processed")
        self.assertIsNotNone((await self.reload(alert)).resolved_at)

    def test_the_wait_between_tries_grows_with_the_outage(self):
        clinic = SimpleNamespace(od_health_status="ok", od_health_changed_at=None, od_health_reason=None,
                                 sync_paused_until=None)
        start = now()
        waits = []
        for minutes in (0, 20, 90):
            at = start + timedelta(minutes=minutes)
            waits.append((clinic_health.mark_od_unreachable(clinic, reason="down", at=at) - at).total_seconds() / 60)
        self.assertEqual(waits, [2, 5, 15])
        self.assertEqual(clinic.od_health_changed_at, start)  # "unreachable since" keeps the first time

    def test_the_clinic_list_flags_an_outage_after_an_hour(self):
        from app.services.dso_clinic_page_service import clinic_attention_reason
        since = datetime(2026, 12, 1, 8, 0, tzinfo=timezone.utc)
        self.clinic.od_health_status, self.clinic.od_health_changed_at = "unreachable", since
        with patch("app.services.dso_clinic_page_service.has_invalid_clinic_config", return_value=False):
            self.assertIsNone(clinic_attention_reason(self.clinic, since + timedelta(minutes=59)))
            self.assertEqual(clinic_attention_reason(self.clinic, since + timedelta(hours=2)),
                             "OpenDental has not answered since 01 Dec 08:00 UTC")


class OurProblemTests(ClinicLineTestCase):
    async def test_our_failures_retry_five_times_then_go_to_the_dead_queue(self):
        john = await self.event("John", "Ade", "09:00")
        self.od.break_last_names.add("Ade")
        seen, waits = [], []
        for _ in range(6):
            await self.run_line()
            john = await self.reload(john)
            seen.append((john.processing_status, john.attempt_count))
            if john.next_attempt_at:
                waits.append(round((utc(john.next_attempt_at) - now()).total_seconds() / 60))
            await self.due_now(john)
        self.assertEqual(seen, [("retrying", 1), ("retrying", 2), ("retrying", 3), ("retrying", 4), ("retrying", 5),
                                ("dead", 6)])
        self.assertEqual(waits, [1, 5, 30, 120, 360])  # 1 min, 5 min, 30 min, 2 h, 6 h
        [alert] = await self.alerts()  # our team hears once, when it reaches the dead queue
        self.assertEqual((alert.audience, alert.code), ("internal", "dead_queue"))
        self.assertIn("tools.sync_line replay", alert.message)
        self.email.assert_not_awaited()  # our problem: the clinic is not emailed ...
        self.assertEqual(await self.notifications(), [])  # ... or notified
        log = await self.sync_log(john)
        self.assertEqual((log.sync_status, log.reason),
                         (SyncStatus.FAILED, "Stopped after 5 retries; our team has been alerted"))

        await self.run_line()  # the dead queue is never resent on its own
        self.assertEqual((await self.reload(john)).processing_status, "dead")

        self.od.break_last_names.clear()  # we fixed the bug, then resend
        self.assertEqual(await worker.replay_dead_events(self.db, by="Funmi", clinic_id=self.clinic.id), 1)
        await self.run_line()
        self.assertEqual((await self.reload(john)).processing_status, "processed")
        self.assertIsNotNone((await self.reload(alert)).resolved_at)

    async def test_a_quick_retry_waits_one_minute_first(self):
        john = await self.event("John", "Ade", "09:00")
        self.od.break_last_names.add("Ade")
        await self.run_line()
        john = await self.reload(john)
        self.assertAlmostEqual((utc(john.next_attempt_at) - now()).total_seconds(), 60, delta=5)
        self.wake.assert_awaited_once_with(self.clinic.id, at=utc(john.next_attempt_at))

    async def test_a_later_change_goes_through_and_replaces_the_one_still_retrying(self):
        booked = await self.event("John", "Ade", "09:00", appointment="john")
        await self.event("Mary", "Bello", "10:00")
        self.od.break_last_names.add("Ade")
        await self.run_line()
        self.assertEqual((await self.reload(booked)).processing_status, "retrying")

        self.od.break_last_names.clear()
        moved = await self.event("John", "Ade", "11:00", appointment="john")  # not held for hours
        await self.run_line()

        self.assertEqual((await self.reload(moved)).processing_status, "processed")
        self.assertEqual((await self.reload(booked)).processing_status, "superseded")
        self.assertEqual([a["AptDateTime"][11:16] for a in self.od.creates], ["10:00", "11:00"])


class ClinicProblemTests(ClinicLineTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.manager = await self.user("manager", RoleType.MANAGER)
        self.staff = await self.user("staff", RoleType.STAFF)

    async def test_a_clinic_problem_emails_the_manager_and_notifies_the_whole_team_once(self):
        first = await self.event("John", "Ade", "09:00", calendar="unmapped")
        second = await self.event("Mary", "Bello", "10:00", calendar="unmapped")
        # Read ids first: the worker's rollback expires every row in this shared session.
        team = sorted([self.owner.id, self.manager.id, self.staff.id])

        await self.run_line()

        for event in (first, second):
            event = await self.reload(event)
            self.assertEqual((event.processing_status, event.attempt_count), ("needs_attention", 1))
            self.assertAlmostEqual((utc(event.next_attempt_at) - now()).total_seconds(), 60, delta=5)
        [alert] = await self.alerts()  # two events, one problem, one alert
        self.assertEqual((alert.audience, alert.code), ("clinic", "chair_mapping"))
        self.assertIn("No chair is mapped to CRM calendar unmapped", alert.message)
        self.email.assert_awaited_once()
        self.assertEqual(self.email.await_args.kwargs["to"], "manager@clinic.example")
        self.assertIn("Sunrise", self.email.await_args.kwargs["subject"])
        self.assertNotIn("John", self.email.await_args.kwargs["body"])  # never patient details
        self.assertEqual(sorted(n.user_id for n in await self.notifications()), team)  # everyone on the team
        log = await self.sync_log(first)
        self.assertEqual((log.sync_status, log.failure_source), (SyncStatus.RETRYING, SyncFailureSource.CUSTOMER_CONFIGURATION))
        self.assertTrue(log.reason.startswith("Needs the clinic: No chair is mapped"))
        self.assertTrue(log.reason.endswith("(5 retries left)"))

        await self.due_now(first, second)
        await self.run_line()  # still not fixed: tried again, nobody told twice
        self.email.assert_awaited_once()
        self.assertEqual(len(await self.notifications()), 3)

    async def test_the_alert_closes_and_the_changes_go_through_once_it_is_fixed(self):
        john = await self.event("John", "Ade", "09:00", calendar="unmapped")
        await self.run_line()
        clinic = await self.fresh_clinic()
        clinic.operatory_calendar_map = {"Scheduled": [{"calendar_id": "unmapped", "operatories": [1]}]}
        await self.db.commit()

        await self.due_now(john)
        await self.run_line()

        self.assertEqual((await self.reload(john)).processing_status, "processed")
        [alert] = await self.alerts()
        self.assertIsNotNone(alert.resolved_at)

    async def test_a_request_opendental_refuses_needs_the_clinic_at_once(self):
        john = await self.event("John", "Ade", "09:00")
        self.od.refuse_last_names.add("Ade")
        await self.run_line()
        john = await self.reload(john)
        self.assertEqual((john.processing_status, john.attempt_count), ("needs_attention", 1))
        [alert] = await self.alerts()
        self.assertEqual(alert.code, "opendental_refused")

    async def test_rejected_keys_are_retried_per_event_and_alert_the_clinic_once(self):
        john = await self.event("John", "Ade", "09:00")
        mary = await self.event("Mary", "Bello", "10:00")
        self.od.keys_rejected = True

        await self.run_line()

        for event in (john, mary):
            event = await self.reload(event)
            self.assertEqual((event.processing_status, event.attempt_count), ("needs_attention", 1))
        clinic = await self.fresh_clinic()
        self.assertEqual((clinic.od_health_status, clinic.sync_paused_until), ("auth_failed", None))
        [alert] = await self.alerts()  # one alert for the clinic's keys, not one per event
        self.assertEqual((alert.code, alert.subject_key), ("opendental_credentials", "clinic"))
        self.email.assert_awaited_once()

        self.od.keys_rejected = False  # the clinic entered new keys
        await self.due_now(john, mary)
        await self.run_line()
        self.assertEqual([(await self.reload(e)).processing_status for e in (john, mary)], ["processed", "processed"])
        self.assertIsNotNone((await self.reload(alert)).resolved_at)

    async def test_a_clinic_problem_reaches_the_dead_queue_but_its_alert_stays_open(self):
        john = await self.event("John", "Ade", "09:00", calendar="unmapped")
        for _ in range(6):
            await self.run_line()
            await self.due_now(await self.reload(john))
        john = await self.reload(john)
        self.assertEqual((john.processing_status, john.attempt_count), ("dead", 6))
        by_code = {alert.code: alert for alert in await self.alerts()}
        self.assertIsNone(by_code["chair_mapping"].resolved_at)  # nothing went through: still open
        self.assertEqual(by_code["dead_queue"].audience, "internal")  # and our team knows it stopped
        self.email.assert_awaited_once()  # the manager was told at the first failure, not six times
        log = await self.sync_log(john)
        self.assertEqual(log.sync_status, SyncStatus.FAILED)
        self.assertTrue(log.reason.startswith("Stopped after 5 retries. Needs the clinic: No chair is mapped"))

        clinic = await self.fresh_clinic()
        clinic.operatory_calendar_map = {"Scheduled": [{"calendar_id": "unmapped", "operatories": [1]}]}
        await self.db.commit()
        await worker.replay_dead_events(self.db, by="Funmi", event_id=john.id)
        self.assertIsNotNone((await self.reload(by_code["dead_queue"])).resolved_at)  # queue empty again
        await self.run_line()
        self.assertEqual((await self.reload(john)).processing_status, "processed")
        self.assertIsNotNone((await self.reload(by_code["chair_mapping"])).resolved_at)

    async def test_once_the_cause_is_fixed_the_clinic_alert_closes_and_only_our_dead_queue_alert_remains(self):
        john = await self.event("John", "Ade", "09:00", calendar="unmapped")
        mary = await self.event("Mary", "Bello", "10:00", calendar="unmapped")
        await self.run_line()  # one shared chair-mapping alert
        john = await self.reload(john)
        john.processing_status, john.attempt_count = "dead", 6  # John's retries ran out earlier
        await self.db.commit()
        clinic = await self.fresh_clinic()
        clinic.operatory_calendar_map = {"Scheduled": [{"calendar_id": "unmapped", "operatories": [1]}]}
        await self.db.commit()

        await self.due_now(await self.reload(mary))
        await self.run_line()  # Mary goes through: the mapping is fixed

        self.assertEqual((await self.reload(mary)).processing_status, "processed")
        [mapping] = [a for a in await self.alerts() if a.code == "chair_mapping"]
        self.assertIsNotNone(mapping.resolved_at)  # the clinic is not told to fix what it fixed
        self.assertEqual((await self.reload(john)).processing_status, "dead")  # resending John is our job

    async def test_an_unclear_booking_waits_for_a_person_and_holds_that_appointments_later_changes(self):
        john = await self.event("John", "Ade", "09:00", appointment="john")
        self.od.lose_reply_to_next_create = True
        await self.run_line()  # the create timed out ...
        self.od.chairs[1].append({**self.od.creates[0], "AptNum": 777})  # ... and OpenDental shows two matches
        moved = await self.event("John", "Ade", "11:00", appointment="john")
        await self.due_now(await self.reload(john))

        await self.run_line()

        john = await self.reload(john)
        self.assertEqual((john.processing_status, john.attempt_count), ("waiting", 1))
        self.assertEqual((await self.reload(moved)).processing_status, "queued")  # a create may exist: wait
        [alert] = [a for a in await self.alerts() if a.code == "outcome_unclear"]
        self.assertEqual(alert.audience, "internal")
        self.assertEqual(len(self.od.creates), 1)  # never booked twice

    async def test_a_newer_change_goes_through_and_replaces_the_one_that_needed_attention(self):
        self.od.occupy(1, "09:00")
        self.od.occupy(2, "09:00")
        booked = await self.event("John", "Ade", "09:00", appointment="john")  # no chair free at 09:00
        await self.run_line()
        self.assertEqual((await self.reload(booked)).processing_status, "needs_attention")

        moved = await self.event("John", "Ade", "11:00", appointment="john")  # John moves to 11:00
        await self.run_line()

        self.assertEqual((await self.reload(moved)).processing_status, "processed")
        booked = await self.reload(booked)
        self.assertEqual(booked.processing_status, "superseded")
        self.assertEqual((await self.sync_log(booked)).is_billable, False)
        [alert] = await self.alerts()
        self.assertIsNotNone(alert.resolved_at)

    async def test_an_email_that_fails_is_tried_again_and_never_stops_the_line(self):
        self.email.side_effect = ConnectionError("smtp down")
        john = await self.event("John", "Ade", "09:00", calendar="unmapped")
        await self.run_line()
        [alert] = await self.alerts()
        self.assertEqual((alert.emailed_at, alert.email_error), (None, "smtp down"))
        self.assertEqual((await self.reload(john)).processing_status, "needs_attention")

        self.email.side_effect = None
        await self.due_now(john)
        await self.run_line()
        self.assertIsNotNone((await self.reload(alert)).emailed_at)

    async def test_without_email_settings_the_team_is_still_notified(self):
        from app.infrastructure.email import send_email
        with patch.object(sync_alerts, "send_email", send_email):  # the real one, SMTP not configured
            await self.event("John", "Ade", "09:00", calendar="unmapped")
            await self.run_line()
        [alert] = await self.alerts()
        self.assertIsNone(alert.emailed_at)
        self.assertIn("Email is not set up", alert.email_error)
        self.assertEqual(len(await self.notifications()), 3)

    async def test_with_no_manager_the_admin_then_the_owner_is_emailed(self):
        clinic = await self.fresh_clinic()
        self.assertEqual([u.email for u in await sync_alerts.clinic_managers(self.db, clinic)], ["manager@clinic.example"])
        for assignment in (await self.db.scalars(select(RoleAssignment))).all():
            assignment.is_active = False
        await self.db.commit()
        self.assertEqual([u.email for u in await sync_alerts.clinic_managers(self.db, clinic)], ["owner@clinic.example"])
        await self.user("admin", RoleType.ADMIN)
        self.assertEqual([u.email for u in await sync_alerts.clinic_managers(self.db, clinic)], ["admin@clinic.example"])

    async def test_the_clinic_list_shows_what_needs_fixing(self):
        from app.services.dso_clinic_page_service import clinic_attention_reason
        await self.event("John", "Ade", "09:00", calendar="unmapped")
        await self.run_line()
        clinic = await self.fresh_clinic()
        found = await sync_alerts.open_clinic_alerts(self.db, [clinic.id])
        with patch("app.services.dso_clinic_page_service.has_invalid_clinic_config", return_value=False):
            reason = clinic_attention_reason(clinic, now(), found.get(clinic.id))
            self.assertTrue(reason.startswith("No chair is mapped to CRM calendar unmapped"))
            self.assertEqual(clinic_attention_reason(clinic, now(), (3, "Oldest problem")),
                             "3 problems are holding CRM changes back. Oldest: Oldest problem")


class DeadQueueTests(ClinicLineTestCase):
    async def dead(self, *events):
        for event in events:
            event.processing_status, event.attempt_count = "dead", 6
        await self.db.commit()

    async def test_replay_needs_a_name_and_only_touches_dead_events(self):
        dead = await self.event("John", "Ade", "09:00")
        waiting = await self.event("Mary", "Bello", "10:00")
        await self.dead(dead)
        with self.assertRaises(ValueError):
            await worker.replay_dead_events(self.db, by=" ", clinic_id=self.clinic.id)

        self.assertEqual(await worker.replay_dead_events(self.db, by="Funmi", clinic_id=self.clinic.id), 1)

        dead, waiting = await self.reload(dead), await self.reload(waiting)
        self.assertEqual((dead.processing_status, dead.attempt_count), ("queued", 0))  # a fresh set of retries
        self.assertEqual(waiting.processing_status, "queued")
        from app.models.entities import Audit_logs
        [audit] = (await self.db.scalars(select(Audit_logs).where(Audit_logs.action == "replayed_from_dead_queue"))).all()
        self.assertEqual(audit.entity_id, str(dead.id))
        self.wake.assert_awaited_with(self.clinic.id)

    async def test_a_newer_change_replaces_a_dead_one(self):
        dead = await self.event("John", "Ade", "09:00", appointment="john")
        await self.dead(dead)
        await self.event("John", "Ade", "11:00", appointment="john")
        await self.run_line()
        self.assertEqual((await self.reload(dead)).processing_status, "superseded")

    async def test_the_cron_wake_skips_disabled_clinics(self):
        await self.event("John", "Ade", "09:00")
        clinic = await self.fresh_clinic()
        clinic.is_disabled = True
        await self.db.commit()
        self.assertEqual(await worker.wake_waiting_clinics(self.db), [])
        self.wake.assert_not_awaited()

        clinic.is_disabled = False
        await self.db.commit()
        self.assertEqual(await worker.wake_waiting_clinics(self.db), [self.clinic.id])


class NoteTests(ClinicLineTestCase):
    async def test_the_note_is_sent_with_the_new_appointment(self):
        await self.event("John", "Ade", "09:00", appointment="john", note="Bring x-rays")
        await self.run_line()
        self.assertEqual(self.od.creates[0]["Note"], "Bring x-rays")

    async def test_a_note_only_change_sends_just_the_note(self):
        await self.event("John", "Ade", "09:00", appointment="john", note="Bring x-rays")
        changed = await self.event("John", "Ade", "09:00", appointment="john", note="Bring x-rays and insurance card")
        await self.run_line()
        apt_num = self.od.creates[0]["AptNum"]
        self.assertEqual(self.od.notes, [(apt_num, "Bring x-rays and insurance card")])
        self.assertEqual(self.od.updates, [])  # the time, chair and status were not sent again
        self.assertEqual((await self.sync_log(changed)).operation, "updated")

    async def test_an_unchanged_or_empty_note_is_not_sent_again(self):
        await self.event("John", "Ade", "09:00", appointment="john", note="Bring x-rays")
        await self.event("John", "Ade", "09:00", appointment="john", note="Bring x-rays")
        await self.event("John", "Ade", "09:00", appointment="john", note="")  # leaves OpenDental's note alone
        await self.run_line()
        self.assertEqual(self.od.notes, [])

    async def test_a_moved_appointment_carries_its_changed_note(self):
        await self.event("John", "Ade", "09:00", appointment="john", note="First visit")
        await self.event("John", "Ade", "11:00", appointment="john", note="Moved: running late")
        await self.run_line()
        [(apt_num, update)] = self.od.updates
        self.assertEqual((update.AptDateTime, update.Note), ("2026-12-01 11:00:00", "Moved: running late"))
        self.assertEqual(self.od.notes, [])

    async def test_the_older_notes_field_still_works(self):
        event = await self.event("John", "Ade", "09:00", appointment="john")
        payload = {**worker.decode_json_secret(event.payload), "Note": "", "Notes": "From the older field"}
        event.payload = encrypt_json_secret(payload)
        await self.db.commit()
        await self.run_line()
        self.assertEqual(self.od.creates[0]["Note"], "From the older field")


class TimedOutBookingTests(ClinicLineTestCase):
    async def journal(self):
        return (await self.db.scalars(select(ExternalOperation).where(
            ExternalOperation.action == "appointment_create").execution_options(populate_existing=True))).all()

    async def test_a_booking_that_did_land_is_found_and_never_made_twice(self):
        john = await self.event("John", "Ade", "09:00")
        self.od.lose_reply_to_next_create = True
        await self.run_line()  # OpenDental created it, but we never heard back
        self.assertEqual((await self.reload(john)).processing_status, "waiting")

        await self.due_now()
        await self.run_line()

        self.assertEqual(len(self.od.creates), 1)
        self.assertEqual((await self.reload(john)).processing_status, "processed")
        row = await self.db.scalar(select(Appointments).filter_by(clinic_id=self.clinic.id))
        self.assertEqual(row.AptNum, self.od.creates[0]["AptNum"])
        [operation] = await self.journal()
        self.assertEqual(operation.state, "applied")

    async def test_a_missing_booking_waits_ten_minutes_then_is_made(self):
        john = await self.event("John", "Ade", "09:00")
        self.od.lose_reply_to_next_create = True
        await self.run_line()
        self.od.chairs = {1: [], 2: []}  # this time it never reached OpenDental
        self.od.creates.clear()

        await self.due_now()
        await self.run_line()
        john = await self.reload(john)
        self.assertEqual((john.processing_status, john.attempt_count), ("waiting", 0))
        self.assertIn("no appointment for this patient", john.failure_reason)
        self.assertEqual(self.od.creates, [])  # not booked while it might still land

        [operation] = await self.journal()
        operation.created_at = now() - timedelta(minutes=11)
        await self.due_now(john)
        await self.run_line()

        self.assertEqual(len(self.od.creates), 1)
        self.assertEqual((await self.reload(john)).processing_status, "processed")
        states = sorted(operation.state for operation in await self.journal())
        self.assertEqual(states, ["applied", "failed"])  # the old attempt is kept as evidence


class NotificationTests(ClinicLineTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.manager = await self.user("manager", RoleType.MANAGER)
        self.staff = await self.user("staff", RoleType.STAFF)
        await self.event("John", "Ade", "09:00", calendar="unmapped")
        await self.run_line()

    async def test_each_member_sees_their_own_notification(self):
        page = await notification_service.list_notifications(self.db, user_id=self.staff.id, unread_only=False, limit=50)
        self.assertEqual(page.unread, 1)
        [item] = page.items
        self.assertEqual((item.clinic_name, item.problem, item.read_at, item.resolved_at),
                         ("Sunrise", "chair_mapping", None, None))
        self.assertIn("No chair is mapped", item.message)

    async def test_reading_marks_only_my_notification(self):
        [mine] = (await notification_service.list_notifications(
            self.db, user_id=self.staff.id, unread_only=True, limit=50)).items
        read = await notification_service.mark_notification_read(self.db, user_id=self.staff.id, notification_id=mine.id)
        self.assertIsNotNone(read.read_at)
        self.assertEqual((await notification_service.list_notifications(
            self.db, user_id=self.manager.id, unread_only=True, limit=50)).unread, 1)  # the manager's is untouched
        with self.assertRaises(NotFound):  # someone else's notification looks like no notification
            await notification_service.mark_notification_read(self.db, user_id=self.manager.id, notification_id=mine.id)

    async def test_read_all(self):
        self.assertEqual(await notification_service.mark_all_read(self.db, user_id=self.manager.id), 1)
        self.assertEqual((await notification_service.list_notifications(
            self.db, user_id=self.manager.id, unread_only=True, limit=50)).items, [])


class WebhookTests(ClinicLineTestCase):
    async def test_the_event_joins_the_line_before_the_job_is_queued(self):
        from app.services import crm_webhook_service as webhook
        event = InboundEvent(clinic_id=self.clinic.id, crm_type="ghl", event_id="e", contact_id="c",
                             payload="p", processing_status="received")
        self.db.add(event)
        await self.db.commit()
        sync_log_id = uuid4()
        seen = {}

        def enqueue(func, *args, **kwargs):
            seen["status"] = event.processing_status  # what the line holds when the job is queued
            seen["call"] = (func, args, kwargs["job_id"])
            seen["timeout"] = kwargs.get("job_timeout")  # a full turn outlasts RQ's 3-minute default
            return SimpleNamespace(id=kwargs["job_id"])

        with patch.object(webhook.appointments_queue, "enqueue", side_effect=enqueue):
            job = await webhook._enqueue_sync_job(self.db, self.clinic, event, crm_type="ghl",
                                                  payload={"secret": "patient"}, sync_log_id=sync_log_id,
                                                  redis_key="k", log_ctx={})

        self.assertEqual(seen["status"], "queued")
        self.assertEqual(seen["call"], (worker.work_through_clinic_job, (self.clinic.id,), job.id))  # no payload
        self.assertEqual(seen["timeout"], 15 * 60)
        self.assertEqual((event.sync_log_id, event.job_id), (sync_log_id, job.id))

    async def test_a_job_that_cannot_be_queued_takes_its_event_out_of_the_line(self):
        from app.services import crm_webhook_service as webhook
        from app.services.errors import OperationFailed
        event = InboundEvent(clinic_id=self.clinic.id, crm_type="ghl", event_id="e", contact_id="c",
                             payload="p", processing_status="received")
        self.db.add(event)
        await self.db.commit()
        with patch.object(webhook.appointments_queue, "enqueue", side_effect=ConnectionError("redis down")), \
             patch.object(webhook, "async_redis", MagicMock(delete=AsyncMock())):
            with self.assertRaises(OperationFailed):
                await webhook._enqueue_sync_job(self.db, self.clinic, event, crm_type="ghl", payload={},
                                                sync_log_id=uuid4(), redis_key="k", log_ctx={})
        self.assertEqual((await self.reload(event)).processing_status, "enqueue_failed")


class ToolTests(ClinicLineTestCase):
    async def tool(self, command):
        import contextlib, io
        from tools import sync_line
        output = io.StringIO()
        session = MagicMock(__aenter__=AsyncMock(return_value=self.db), __aexit__=AsyncMock(return_value=False))
        with patch.object(sync_line, "AsyncSessionLocal", return_value=session), contextlib.redirect_stdout(output):
            await (sync_line.show_status() if command == "status" else sync_line.wake_all())
        return output.getvalue()

    async def test_status_shows_waiting_events_the_pause_and_open_alerts(self):
        await self.event("Ann", "Cole", "08:00", calendar="unmapped")
        await self.run_line()  # needs the clinic: an open alert
        await self.event("John", "Ade", "09:00")
        self.od.down = True
        await self.run_line()  # John waits for OpenDental
        await self.event("Mary", "Bello", "10:00")

        report = await self.tool("status")

        self.assertIn("Sunrise", report)
        self.assertIn("OpenDental paused until", report)
        self.assertRegex(report, r"waiting\s+1")
        self.assertRegex(report, r"queued\s+1")
        self.assertRegex(report, r"needs_attention\s+1")
        self.assertIn("chair_mapping", report)
        self.assertIn("emailed", report)  # no manager here, so the owner was emailed

    async def test_status_shows_the_dead_queue_and_how_to_resend(self):
        john = await self.event("John", "Ade", "09:00")
        john.processing_status = "dead"
        await self.db.commit()
        report = await self.tool("status")
        self.assertRegex(report, r"dead\s+1\s+oldest .*replay --clinic")

    async def test_wake_queues_one_job_per_clinic_with_waiting_events(self):
        await self.event("John", "Ade", "09:00")
        await self.event("Mary", "Bello", "10:00")
        report = await self.tool("wake")
        self.wake.assert_awaited_once_with(self.clinic.id)
        self.assertIn("1 clinic line", report)


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()

    async def asyncTearDown(self):
        await close_local_session(self.db, self.engine)

    async def ready(self):
        from app.infrastructure import database
        with patch.object(database, "engine", self.engine):
            return await database.ping_db(check_schema=True)

    async def test_ready_when_the_schema_has_what_the_code_reads(self):
        self.assertEqual(await self.ready(), (True, "DB connection OK"))

    async def test_not_ready_when_a_migration_is_missing(self):
        from sqlalchemy import text
        async with self.engine.begin() as connection:
            await connection.execute(text("DROP TABLE user_notifications"))
        self.assertEqual(await self.ready(), (False, "Database schema upgrade required"))


if __name__ == "__main__":
    unittest.main()
