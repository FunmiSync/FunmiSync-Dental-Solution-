"""Regression tests exercise real modules with fake DB/Redis/provider boundaries.

Run with: .venv/bin/python -B -m unittest discover -s tests -v
"""
from datetime import datetime, timedelta, timezone
import fnmatch
import json
import logging
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment

configure_test_environment()

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.dialects import postgresql

from auth import oauth2, login_helper
from auth.security import encrypt_secret
from caches import sync_log_cache, toroforge_billing_cache, operatory_cache
from core.middleware import RateLimitMiddleware
from core.models import RegisteredClinics, Wallet, PaymentTransaction
from core.schemas import AppointmentRequest
from core import utils
from infra.appointment_service import AppointmentService, CustomerConfigurationSyncError
from infra.sync_log_service import build_clinic_items, _encode_cursor, what_happened
from sdk.opendental_sdk import openDentalApi, OpenDentalAuthError, OpenDentalUnavailableError


_service_guards = []


def setUpModule():
    # Fail immediately if a regression test accidentally reaches a real service.
    for target in ("core.database.engine.connect", "redis.Redis.execute_command",
                   "redis.asyncio.Redis.execute_command", "httpx.AsyncClient.send"):
        guard = patch(target, side_effect=AssertionError("Real service access is forbidden in regression tests"))
        guard.start()
        _service_guards.append(guard)


def tearDownModule():
    for guard in reversed(_service_guards):
        guard.stop()


def clinic(**overrides):
    values = dict(id=uuid4(), dso_id=None, is_disabled=False, clinic_name="Test",
                  clinic_timezone="UTC", clinic_number=1,
                  webhook_secret=encrypt_secret("webhook-secret"),
                  od_developer_key=encrypt_secret("developer"), od_customer_key=encrypt_secret("customer"))
    return SimpleNamespace(**(values | overrides))


def request_data():
    return AppointmentRequest(date_str="2026-10-17", start_str="09:00", end_str="09:30",
                              status="Scheduled", calendar_id="calendar", event_id="event",
                              contact_id="contact", pat_id=uuid4(), pat_Num=10,
                              clinic_timezone="UTC", commslog="Call completed", pop_up="Reminder")


class AppointmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_duration_round_trip_and_plain_or_legacy_timestamps(self):
        start, end, pattern = await utils.opendental_pattern_time_build("2026-10-17", "09:00", "09:30", "UTC")
        self.assertEqual(pattern, "XXXXXX")
        for suffix in ("", " "):
            self.assertEqual(utils.get_pattern_from_od("2026-10-17 09:00:00" + suffix, pattern), end)
        self.assertEqual(start, datetime(2026, 10, 17, 9))

    async def test_invalid_durations_and_dst_are_rejected(self):
        for start, end in (("09:00", "09:00"), ("09:30", "09:00"), ("09:00", "09:07"), ("00:00", "10:00")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                await utils.opendental_pattern_time_build("2026-10-17", start, end, "UTC")
        import pytz
        with self.assertRaises(pytz.NonExistentTimeError):
            await utils.opendental_pattern_time_build("2026-03-08", "02:00", "02:30", "America/New_York")
        with self.assertRaises(ValueError):
            await utils.opendental_pattern_time_build("2026-03-08", "01:30", "03:30", "America/New_York")

    async def test_update_excludes_self_but_not_other_bookings(self):
        start, end = datetime(2026, 10, 17, 9), datetime(2026, 10, 17, 9, 30)
        own = {"AptNum": "12", "AptDateTime": "2026-10-17 09:00:00", "Pattern": "XXXXXX"}
        self.assertTrue(await utils.check_time_slot([own], start, end, exclude_apt_num=12))
        self.assertFalse(await utils.check_time_slot([own | {"AptNum": 13}], start, end, exclude_apt_num=12))
        self.assertTrue(await utils.check_time_slot([own], end, end + timedelta(minutes=30)))

    async def test_cache_miss_awaits_provider_once_and_commits_health(self):
        db = MagicMock()
        od = SimpleNamespace(get_appointments_in_operatory=AsyncMock(return_value=[]))
        service = AppointmentService(db, clinic(), od)
        with patch("infra.appointment_service.get_operatory_day_appointments_cached", return_value=None), \
             patch("infra.appointment_service.set_operatory_day_appointments_cached") as save:
            self.assertEqual(await service.get_operatory_appointments_cached(operatory=1, date_start="2026-10-17", date_end="2026-10-17"), [])
        od.get_appointments_in_operatory.assert_awaited_once()
        db.commit.assert_called_once()
        save.assert_called_once()

    async def test_unchanged_booking_resumes_side_effects_exactly_once(self):
        db = MagicMock()
        service = AppointmentService(db, clinic(), MagicMock())
        req = request_data()
        reserve = SimpleNamespace(AptNum=12, status=req.status, date=req.date_str,
                                  start_time="2026-10-17 09:00:00 ", end_time="2026-10-17 09:30:00 ",
                                  calendar_id=req.calendar_id, commslog_done=False, popups_done=False)
        service.book_reserve = AsyncMock(return_value=reserve)
        service.handle_commslog = AsyncMock()
        service.handle_popups = AsyncMock()
        service.operations = MagicMock()  # side-effect helpers are stubbed in this unit test
        service.get_operatories = AsyncMock()
        for _ in range(2):
            result = await service.book(req)
            self.assertEqual(result.action, "unchanged")
        service.handle_commslog.assert_awaited_once()
        service.handle_popups.assert_awaited_once()
        service.get_operatories.assert_not_awaited()
        self.assertTrue(reserve.popups_done)

    async def test_disabled_clinic_stops_before_any_provider_call(self):
        od = MagicMock()
        service = AppointmentService(MagicMock(), clinic(is_disabled=True), od)
        with self.assertRaises(CustomerConfigurationSyncError):
            await service.book(request_data())
        self.assertEqual(od.mock_calls, [])


class OpenDentalTests(unittest.IsolatedAsyncioTestCase):
    def make_sdk(self, row=None):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = row or clinic()
        with patch("sdk.opendental_sdk.SessionLocal", return_value=db):
            sdk = openDentalApi((row or clinic()).id)
        return sdk, db

    async def test_credentials_are_decrypted_and_session_is_closed(self):
        row = clinic()
        sdk, db = self.make_sdk(row)
        self.assertEqual(sdk.headers["Authorization"], "ODFHIR developer/customer")
        self.assertEqual(sdk.base_url, "https://api.opendental.com/api/v1")
        db.close.assert_called_once()
        other, _ = self.make_sdk(clinic())
        self.assertIsNot(sdk.cb, other.cb)

    async def test_bad_ciphertext_closes_session(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = clinic(od_developer_key="broken")
        with patch("sdk.opendental_sdk.SessionLocal", return_value=db), self.assertRaises(OpenDentalAuthError):
            openDentalApi(uuid4())
        db.close.assert_called_once()

    async def test_auth_failure_is_not_retried(self):
        sdk, _ = self.make_sdk()
        response = httpx.Response(401, request=httpx.Request("GET", "https://provider.invalid/patients"))
        client = MagicMock()
        client.request = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("sdk.opendental_sdk.httpx.AsyncClient", return_value=client), \
             self.assertRaises(OpenDentalAuthError):
            await sdk._request("GET", "/patients")
        client.request.assert_awaited_once()

    async def test_timed_out_write_is_not_replayed(self):
        sdk, _ = self.make_sdk()
        client = MagicMock()
        client.request = AsyncMock(side_effect=httpx.ReadTimeout("uncertain result"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("sdk.opendental_sdk.httpx.AsyncClient", return_value=client), self.assertRaises(OpenDentalUnavailableError):
            await sdk._request("POST", "/patients", json={})
        client.request.assert_awaited_once()

    async def test_transient_read_retries_and_preserves_final_error(self):
        error = httpx.HTTPStatusError("unavailable", request=httpx.Request("GET", "https://provider.invalid"),
                                     response=httpx.Response(503))
        call = AsyncMock(side_effect=error)
        with patch("core.utils.asyncio.sleep", new_callable=AsyncMock) as sleep, self.assertRaises(httpx.HTTPStatusError):
            await utils.retry_with_bak_off(call, retries=3)
        self.assertEqual(call.await_count, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_update_uses_documented_resource(self):
        sdk, _ = self.make_sdk()
        sdk._request = AsyncMock(return_value={})
        from core.schemas import Appointments_update
        await sdk.update_appointment(12, Appointments_update(Pattern="XXXXXX", AptDateTime="2026-10-17 09:00:00",
                                                            Op="1", AptStatus="Scheduled"))
        self.assertEqual(sdk._request.call_args.args, ("PUT", "/appointments/12"))

    async def test_toroforge_transient_http_reads_retry_but_auth_does_not(self):
        from billing.toroforge.toroforge_client.client import ToroForgeClient
        from billing.toroforge.exceptions import ToroForgeHTTPError, ToroForgeAuthError
        client = object.__new__(ToroForgeClient)
        for code in (408, 429, 500, 502, 503, 504):
            self.assertTrue(client.should_retry_read(ToroForgeHTTPError(f"HTTP failure: {code}")))
        self.assertFalse(client.should_retry_read(ToroForgeAuthError("HTTP failure: 401")))


class DisabledWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_job_is_not_sent_or_retried_or_billed(self):
        from workers import workers
        db = MagicMock()
        log = SimpleNamespace(id=uuid4())
        row = clinic(is_disabled=True)
        db.query.return_value.filter_by.return_value.first.side_effect = [log, row]
        logs = MagicMock()
        with patch("workers.workers.SessionLocal", return_value=db), \
             patch("workers.workers.get_current_job", return_value=None), \
             patch("workers.workers.AppointmentSyncLogService", return_value=logs), \
             patch("workers.workers.openDentalApi") as provider:
            result = await workers.process_crm_load(row.id, "ghl", {}, log.id)
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(logs.mark_failure.call_args.kwargs["billable"])
        self.assertFalse(logs.mark_failure.call_args.kwargs["should_retry"])
        provider.assert_not_called()
        db.close.assert_called_once()

    async def test_explicit_nonbillable_failure_is_persisted(self):
        from infra.appointment_sync_log_helper import AppointmentSyncLogService
        from core.models import AppointmentSyncLog, SyncFailureSource
        db = MagicMock()
        log = AppointmentSyncLog()
        service = AppointmentSyncLogService(db)
        with patch.object(service, "publish_change"):
            service.mark_failure(log, reason="Clinic is disabled", should_retry=False, operation=None,
                                 failure_source=SyncFailureSource.CUSTOMER_CONFIGURATION, billable=False)
        self.assertFalse(log.is_billable)
        self.assertFalse(log.counts_toward_usage)
        db.commit.assert_called_once()


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_headers_do_not_crash_or_accept_other_schemes(self):
        middleware = RateLimitMiddleware(MagicMock())
        for header in (None, "Bearer", "Bearer ", "BearerFake token", "Bearer a b"):
            request = SimpleNamespace(headers={} if header is None else {"Authorization": header}, cookies={})
            with self.subTest(header=header):
                self.assertIsNone(middleware._get_user_id_from_token(request))
                self.assertIsNone(oauth2.get_stream_token_from_request(request))
        with patch("core.middleware.decode_token", return_value={"id": "user", "type": "access"}):
            self.assertEqual(middleware._get_user_id_from_token(SimpleNamespace(headers={"Authorization": "bearer token"})), "user")

    async def test_spoofed_forwarding_header_does_not_change_identity(self):
        request = SimpleNamespace(headers={"x-forwarded-for": "attacker", "X_forwarded_for": "attacker"},
                                  client=SimpleNamespace(host="127.0.0.2"))
        self.assertEqual(RateLimitMiddleware(MagicMock())._get_client_ip(request), "127.0.0.2")
        self.assertEqual(login_helper.get_client_ip(request), "127.0.0.2")

    async def test_login_counter_uses_atomic_expiring_increment(self):
        redis = SimpleNamespace(eval=AsyncMock(side_effect=[1, 2, 5]))
        with patch("auth.login_helper.async_redis", redis):
            for _ in range(3):
                await login_helper.increment_attempts_with_key("test-key")
        from app.infrastructure.rate_counter import COUNTER_SCRIPT
        self.assertEqual(redis.eval.await_count, 3)
        redis.eval.assert_awaited_with(COUNTER_SCRIPT, 1, "test-key", login_helper.LOCK_WINDOWS_SECONDS)

    async def test_inactive_account_cannot_refresh(self):
        user = SimpleNamespace(token_version=1, refresh_jti="jti", is_active=False)
        db = MagicMock()
        db.query.return_value.filter.return_value.with_for_update.return_value.populate_existing.return_value.first.return_value = user
        payload = {"id": str(uuid4()), "type": "refresh", "token_version": 1, "jti": "jti"}
        with patch("auth.oauth2.decode_token", return_value=payload), self.assertRaises(HTTPException) as caught:
            oauth2.validate_refresh_token("token", db)
        self.assertEqual(caught.exception.status_code, 401)


class CacheAndPagingTests(unittest.TestCase):
    def test_history_ranges_and_all_pages_are_invalidated_without_cross_scope_deletes(self):
        cid, did, other = uuid4(), uuid4(), uuid4()
        date = datetime(2020, 1, 1).date()
        keys = {sync_log_cache.page_cache_key(scope="clinic", scope_id=id_, clinic_filter_id=None,
                status=None, search=None, date_from=date, date_to=date, cursor="next", limit=50): "value"
                for id_ in (cid, other)}
        keys[f"sync_logs:summary:dso:{did}:clinic:none:from:2020-01-01:to:2026-10-17"] = "value"
        def scan(*, match):
            return [key for key in list(keys) if fnmatch.fnmatch(key, match)]
        fake = SimpleNamespace(scan_iter=scan, delete=lambda key: keys.pop(key, None))
        with patch("caches.sync_log_cache.redis_client", fake):
            sync_log_cache.invalidate_hot_sync_log_cache(dso_id=did, clinic_id=cid)
        self.assertEqual(len(keys), 1)
        self.assertIn(str(other), next(iter(keys)))

    def test_cache_outage_is_optional(self):
        fake = MagicMock()
        fake.get.side_effect = RedisConnectionError("offline")
        fake.delete.side_effect = RedisConnectionError("offline")
        with patch("caches.operatory_cache.redis_client", fake), self.assertLogs("app.infrastructure.cache.resilience", level="WARNING"):
            self.assertIsNone(operatory_cache.get_operatory_day_appointments_cached(clinic_id=uuid4(), operatory=1,
                              date_start="2026-10-17", date_end="2026-10-17"))
        with patch("caches.toroforge_billing_cache.redis_client", fake), self.assertLogs("app.infrastructure.cache.resilience", level="WARNING"):
            toroforge_billing_cache.invalidate_billing_scope(clinic_id=uuid4(), dso_id=uuid4())
        self.assertEqual(fake.delete.call_count, 2)

    def test_clinic_cursor_is_an_or_condition(self):
        from core.database import SessionLocal
        db = SessionLocal()
        captured = []
        from sqlalchemy.orm import Query
        def capture(query):
            captured.append(str(query.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
            return []
        try:
            with patch.object(Query, "all", capture):
                build_clinic_items(db, clinic_id=uuid4(), status=None, search=None, limit=50,
                                   date_from=None, date_to=None,
                                   cursor=_encode_cursor(datetime.now(timezone.utc), uuid4()))
        finally:
            db.close()
        self.assertIn(" OR ", captured[0])
        self.assertIn("appointment_sync_logs.id <", captured[0])

    def test_completed_operations_have_specific_labels(self):
        for operation, expected in (("created", "Appointment Created"), ("updated", "Appointment Updated")):
            self.assertEqual(what_happened(SimpleNamespace(operation=operation, appointment_status="Scheduled")), expected)

    def test_wallet_commit_invalidates_clinic_and_dso_views(self):
        from billing.toroforge.toroforge_service.wallet_service import ToroForgeWalletService
        row = SimpleNamespace(clinic_id=uuid4(), dso_id=uuid4())
        service = ToroForgeWalletService(MagicMock(), MagicMock(), MagicMock())
        with patch("billing.toroforge.toroforge_service.wallet_service.invalidate_billing_scope") as invalidate:
            service.commit_wallet(row, action="test", log_ctx={})
        invalidate.assert_called_once_with(clinic_id=row.clinic_id, dso_id=row.dso_id)

    def test_funding_commit_invalidates_affected_scope(self):
        from billing.toroforge.toroforge_service.funding_service import ToroForgeFundingService
        from app.models.entities import PaymentTransactionStatus
        row = PaymentTransaction(id=uuid4(),clinic_id=uuid4(),dso_id=uuid4(),status=PaymentTransactionStatus.PENDING)
        service = ToroForgeFundingService(MagicMock(), MagicMock())
        with patch("billing.toroforge.toroforge_service.funding_service.invalidate_billing_scope") as invalidate:
            service.commit_and_refresh(row, action="test", log_ctx={})
        invalidate.assert_called_once_with(clinic_id=row.clinic_id, dso_id=row.dso_id)

    def test_invalid_cached_json_is_a_miss(self):
        fake = MagicMock()
        fake.get.return_value = "{broken"
        with patch("caches.toroforge_billing_cache.redis_client", fake), self.assertLogs("app.infrastructure.cache.resilience", level="WARNING"):
            self.assertIsNone(toroforge_billing_cache.cache_get_json("test"))


class ApiBehaviorTests(unittest.TestCase):
    def setUp(self):
        from main import app
        from core.database import get_db
        self.app = app
        self.db = MagicMock()
        self.user = SimpleNamespace(id=uuid4())
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[oauth2.get_current_user] = lambda: self.user
        redis = SimpleNamespace(eval=AsyncMock(return_value=1))
        self.rate_patch = patch("core.middleware.async_redis", redis)
        self.rate_patch.start()
        # No lifespan context: tests must not run the real startup database probe.
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.rate_patch.stop()

    def test_dso_default_pagination_and_bounds(self):
        result = dict(generated_at=datetime.now(timezone.utc), visible_count=0,
                      summary=dict(synced_today=0, in_progress=0, needs_attention=0, failed=0),
                      clinics=[], items=[], next_cursor=None)
        url = f"/dsos/{uuid4()}/sync-logs"
        with patch("api.routers.synclog.sync_log_dso.require_dso_access"), \
             patch("api.routers.synclog.sync_log_dso.build_dso_page_snapshot_cached", return_value=result) as build:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(build.call_args.kwargs["limit"], 50)
            for limit in (0, -1, 201):
                self.assertEqual(self.client.get(url, params={"limit": limit}).status_code, 422)
        self.assertEqual(set(response.json()), {"generated_at", "visible_count", "summary", "clinics", "items", "next_cursor"})

    def test_runtime_openapi_preserves_original_contract_except_reviewed_limit(self):
        from tools.api_contract import ROOT
        expected = json.loads((ROOT / "docs/api_openapi_baseline.json").read_text())
        parameters = expected["paths"]["/dsos/{dso_id}/sync-logs"]["get"]["parameters"]
        limit = next(item for item in parameters if item["name"] == "limit")
        self.assertNotIn("maximum", limit["schema"])
        limit["schema"].update(default=50, minimum=1, maximum=200)
        expected["paths"]["/health/ready"] = {"get":{"tags":["Health"],"summary":"Readiness",
            "operationId":"readiness_health_ready_get","responses":{"200":{"description":"Successful Response",
            "content":{"application/json":{"schema":{}}}}}}}
        self.assertEqual(expected, self.app.openapi())

    def test_disabled_webhook_does_not_enqueue(self):
        row = clinic(is_disabled=True, crm_type="ghl")
        self.db.query.return_value.filter_by.return_value.first.return_value = row
        payload = dict(event_id="event", contact_id="contact", commslog="", status="Scheduled",
                       Date="2026-10-17", start_time="09:00", end_time="09:30", first_name="Test",
                       last_name="Patient", BirthDate="2000-01-01", Gender="Male", calendar_id="calendar",
                       Note="", WirelessPhone="000", Email="patient@example.com", PriProv="1")
        with patch("api.routers.webhook.webhook_crm.verify_webhook_secret_header"), \
             patch("api.routers.webhook.webhook_crm.appointments_queue.enqueue") as enqueue:
            response = self.client.post(f"/webhook/ghl/{row.id}", json=payload)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Clinic is disabled")
        enqueue.assert_not_called()

    def test_malformed_bearer_header_keeps_health_route_available(self):
        response = self.client.get("/", headers={"Authorization": "Bearer"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("x-request-id", response.headers)

    def test_refresh_keeps_response_keys_and_cookie_settings(self):
        from core.models import Users
        from auth.login import router
        from fastapi import FastAPI
        from core.database import get_db
        app = FastAPI()
        app.include_router(router)
        user = Users(id=uuid4(), username="Test", email="test@example.com", password="unused",
                     is_active=True, token_version=1, refresh_jti="old")
        app.dependency_overrides[get_db] = lambda: self.db
        with TestClient(app, base_url="https://backend.invalid") as client, \
             patch("auth.login.validate_refresh_token", return_value=user):
            client.cookies.set("refresh_token", "old-token", path="/login/refresh")
            client.cookies.set("csrf_token", "csrf", path="/")
            response = client.post("/login/refresh", headers={"X-CSRF-Token": "csrf"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()), {"access_token", "csrf_token"})
        headers = response.headers.get_list("set-cookie")
        self.assertEqual(len(headers), 3)
        for cookie in headers:
            self.assertIn("SameSite=none", cookie)
            self.assertIn("Secure", cookie)
        refresh = next(cookie for cookie in headers if cookie.startswith("refresh_token="))
        self.assertIn("Path=/login/refresh", refresh)
        self.assertIn("HttpOnly", refresh)
