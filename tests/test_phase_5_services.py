"""Behaviour of the services extracted from controllers in Phase 5.

Runs against in-memory SQLite and mocks; no PostgreSQL, Redis or provider access.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment, guarded_local_services
configure_test_environment()

from fastapi.testclient import TestClient

from sqlalchemy import func, select
from local_database import close_local_session, fake_async_session, local_session
from app.models.entities import Dso, MemberInvite, RegisteredClinics, RoleAssignment, RoleType, ScopeType, Users
from app.schemas.api import cliniccreate
from app.security.encryption import decode_secret, encrypt_secret
from app.security.oauth2 import hashpassword
from app.services import invite_service
from app.services.auth.authentication import authenticate_with_google, authenticate_with_password
from app.services.errors import AuthenticationFailed, Gone, NotFound, PermissionDenied, TooManyRequests
from app.services.registration import RegistrationConflict, register_dso_clinic, register_standalone_clinic
from app.services.workspace_service import build_my_workspaces

_guard = None


def setUpModule():
    global _guard
    _guard = guarded_local_services()
    _guard.__enter__()


def tearDownModule():
    _guard.__exit__(None, None, None)


def record_thread(calls, *, result):
    """Stand-in for a slow function that records whether it ran on the event loop."""
    def record(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            calls.append("event loop")
        except RuntimeError:
            calls.append("worker thread")
        return result
    return record


def naive_utc_now():
    # SQLite drops tzinfo when reading timestamps back; PostgreSQL timestamptz keeps it.
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clinic_payload(**changes):
    values = dict(crm_type="ghl", clinic_name="Main Street", clinic_number=7, clinic_timezone="America/New_York",
                  od_developer_key="developer-key", od_customer_key="customer-key", crm_api_key="crm-key",
                  location_id="location", calendar_id="calendar",
                  operatory_calendar_map={"Scheduled": [{"calendar_id": "calendar", "operatories": [1]}]})
    values.update(changes)
    return cliniccreate(**values)


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()

    async def asyncTearDown(self):
        await close_local_session(self.db, self.engine)

    async def all_rows(self, model, **filters):
        return (await self.db.scalars(select(model).filter_by(**filters))).all()

    async def count(self, model):
        return await self.db.scalar(select(func.count()).select_from(model))

    async def user(self, email, **values):
        row = Users(id=uuid4(), username=email.split("@")[0], email=email, password="unused", **values)
        self.db.add(row)
        await self.db.commit()
        return row

    async def membership(self, user, role, *, dso=None, clinic=None):
        row = RoleAssignment(id=uuid4(), user_id=user.id, role=role, created_by=user.id,
                             scope_type=ScopeType.CLINIC if clinic else ScopeType.DSO,
                             dso_id=dso.id if dso else None, clinic_id=clinic.id if clinic else None)
        self.db.add(row)
        await self.db.commit()
        return row

    async def dso(self, owner, name="Bright Smiles"):
        row = Dso(id=uuid4(), name=name, user_id=owner.id)
        self.db.add(row)
        await self.db.commit()
        await self.membership(owner, RoleType.ADMIN, dso=row)
        return row

    async def clinic(self, owner, *, name="Main Street", dso=None):
        row = RegisteredClinics(id=uuid4(), crm_type="ghl", clinic_name=name, clinic_number=1, clinic_timezone="UTC",
                                od_developer_key=encrypt_secret("key"), od_customer_key=encrypt_secret("key"),
                                crm_api_key=encrypt_secret("key"), location_id="location",
                                calendar_id=encrypt_secret("calendar"), owner_id=owner.id,
                                dso_id=dso.id if dso else None)
        self.db.add(row)
        await self.db.commit()
        return row


class ClinicRegistrationTests(ServiceTestCase):
    async def test_dso_clinic_is_owned_by_dso_owner_and_administered_by_creator(self):
        owner, manager = await self.user("owner@example.com"), await self.user("manager@example.com")
        dso = await self.dso(owner)
        with patch("app.services.registration.invalidate_dso_clinic_list_cache") as invalidate:
            clinic = await register_dso_clinic(self.db, dso=dso, payload=clinic_payload(), actor_user_id=manager.id)

        self.assertEqual((clinic.owner_id, clinic.dso_id), (owner.id, dso.id))
        [admin] = await self.all_rows(RoleAssignment, clinic_id=clinic.id)
        self.assertEqual((admin.user_id, admin.role, admin.is_active), (manager.id, RoleType.ADMIN, True))
        invalidate.assert_called_once_with(dso_id=dso.id)

    async def test_clinic_credentials_and_webhook_secret_are_stored_encrypted(self):
        owner = await self.user("owner@example.com")
        clinic = await register_standalone_clinic(self.db, payload=clinic_payload(), owner_id=owner.id)
        for column, plaintext in (("crm_api_key", "crm-key"), ("od_developer_key", "developer-key"),
                                  ("od_customer_key", "customer-key"), ("calendar_id", "calendar")):
            with self.subTest(column=column):
                self.assertNotEqual(getattr(clinic, column), plaintext)
                self.assertEqual(decode_secret(getattr(clinic, column)), plaintext)
        self.assertTrue(decode_secret(clinic.webhook_secret))
        self.assertEqual(clinic.operatory_calendar_map, {"Scheduled": [{"calendar_id": "calendar", "operatories": [1]}]})

    async def test_duplicates_are_rejected_without_writing_a_second_clinic(self):
        owner = await self.user("owner@example.com")
        dso = await self.dso(owner)
        await register_standalone_clinic(self.db, payload=clinic_payload(), owner_id=owner.id)
        with self.assertRaisesRegex(RegistrationConflict, "You Already have a clinic created already"):
            await register_standalone_clinic(self.db, payload=clinic_payload(clinic_number=8), owner_id=owner.id)

        with patch("app.services.registration.invalidate_dso_clinic_list_cache"):
            await register_dso_clinic(self.db, dso=dso, payload=clinic_payload(), actor_user_id=owner.id)
            with self.assertRaisesRegex(RegistrationConflict, "Clinic already exist in DSO"):
                await register_dso_clinic(self.db, dso=dso, payload=clinic_payload(clinic_name="Other"), actor_user_id=owner.id)
        self.assertEqual(await self.count(RegisteredClinics), 2)


@patch.object(invite_service, "_now", naive_utc_now)
@patch.object(invite_service, "invalidate_dso_team_members_cache")
@patch.object(invite_service, "invalidate_clinic_team_members_cache")
class InviteServiceTests(ServiceTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.owner = await self.user("owner@example.com")
        self.dso_row = await self.dso(self.owner)
        [self.admin] = await self.all_rows(RoleAssignment, user_id=self.owner.id)

    async def invite_dso(self, email="new@example.com", role="staff", actor=None):
        return await invite_service.create_dso_invite(self.db, dso_id=self.dso_row.id, actor=actor or self.admin,
                                                email=email, role=role, created_by=self.owner.id)

    async def test_a_new_invite_revokes_the_pending_one_and_stores_only_a_hash(self, *_):
        first = await self.invite_dso()
        second = await self.invite_dso(email="  NEW@example.com ")
        invites = {row.token_hash: row for row in await self.all_rows(MemberInvite)}
        self.assertEqual(len(invites), 2)
        self.assertIsNotNone(invites[invite_service.hash_token(first["invite_token"])].revoked_at)
        latest = invites[invite_service.hash_token(second["invite_token"])]
        self.assertIsNone(latest.revoked_at)
        self.assertEqual(latest.email, "new@example.com")
        self.assertNotIn(second["invite_token"], invites)
        self.assertEqual(second["message"], "Dso invite created")

    async def test_managers_may_invite_staff_but_not_other_managers(self, *_):
        manager = SimpleNamespace(role=RoleType.MANAGER)
        with self.assertRaisesRegex(PermissionDenied, "Manager can only invite staff"):
            await self.invite_dso(role="manager", actor=manager)
        self.assertEqual(await self.count(MemberInvite), 0)
        await self.invite_dso(role="staff", actor=manager)

        # A DSO-level manager is also limited when inviting into one of the DSO's clinics.
        clinic = await self.clinic(self.owner, dso=self.dso_row)
        dso_manager = await self.user("manager@example.com")
        await self.membership(dso_manager, RoleType.MANAGER, dso=self.dso_row)
        with self.assertRaisesRegex(PermissionDenied, "Manger can only invite staff"):
            await invite_service.create_clinic_invite(self.db, clinic=clinic, email="x@example.com", role="manager",
                                                created_by=dso_manager.id)
        result = await invite_service.create_clinic_invite(self.db, clinic=clinic, email="x@example.com", role="staff",
                                                     created_by=dso_manager.id)
        self.assertEqual(result["message"], "Clinic invite created")

    async def test_accept_creates_one_membership_and_consumes_the_token(self, clinic_cache, dso_cache):
        invitee = await self.user("new@example.com")
        token = (await self.invite_dso())["invite_token"]

        self.assertEqual(await invite_service.accept_invite(self.db, token=token, user=invitee),
                         {"message": "Invite accepted successfully"})
        [membership] = await self.all_rows(RoleAssignment, user_id=invitee.id)
        self.assertEqual((membership.scope_type, membership.dso_id, membership.role),
                         (ScopeType.DSO, self.dso_row.id, RoleType.STAFF))
        self.assertEqual(membership.created_by, self.owner.id)
        dso_cache.assert_called_once_with(dso_id=self.dso_row.id)
        clinic_cache.assert_not_called()

        with self.assertRaisesRegex(NotFound, "Invalid invite token"):
            await invite_service.accept_invite(self.db, token=token, user=invitee)

    async def test_accept_rejects_another_users_email_and_expired_tokens(self, *_):
        stranger = await self.user("stranger@example.com")
        token = (await self.invite_dso())["invite_token"]
        with self.assertRaisesRegex(PermissionDenied, "Invite email mismatch"):
            await invite_service.accept_invite(self.db, token=token, user=stranger)

        [invite] = await self.all_rows(MemberInvite)
        invite.expires_at = naive_utc_now() - timedelta(minutes=1)
        await self.db.commit()
        invitee = await self.user("new@example.com")
        with self.assertRaisesRegex(Gone, "Invite token expired"):
            await invite_service.accept_invite(self.db, token=token, user=invitee)
        self.assertEqual(len(await self.all_rows(RoleAssignment, user_id=invitee.id)), 0)

    async def test_accepting_into_an_existing_membership_does_not_duplicate_it(self, *_):
        invitee = await self.user("new@example.com")
        await self.membership(invitee, RoleType.STAFF, dso=self.dso_row)
        token = (await self.invite_dso())["invite_token"]
        self.assertEqual(await invite_service.accept_invite(self.db, token=token, user=invitee),
                         {"message": "Already a member in this scope"})
        self.assertEqual(len(await self.all_rows(RoleAssignment, user_id=invitee.id)), 1)
        [invite] = await self.all_rows(MemberInvite)
        self.assertIsNotNone(invite.accepted_at)


class WorkspaceServiceTests(ServiceTestCase):
    async def test_dsos_come_first_and_owned_standalone_clinics_are_listed(self):
        owner = await self.user("owner@example.com")
        await self.dso(owner, name="Zeta Group")
        await self.clinic(owner, name="Alpha Dental")

        result = await build_my_workspaces(self.db, user=owner)
        self.assertEqual(result["workspace_count"], 2)
        self.assertEqual([(item.scope_type, item.access_source) for item in result["workspaces"]],
                         [("dso", "owner"), ("clinic", "owner")])
        self.assertIsNone(result["default_workspace"])
        self.assertEqual(result["username"], "owner")

    async def test_a_single_clinic_membership_becomes_the_default_workspace(self):
        owner, staff = await self.user("owner@example.com"), await self.user("staff@example.com")
        dso = await self.dso(owner)
        clinic = await self.clinic(owner, dso=dso)
        await self.membership(staff, RoleType.STAFF, clinic=clinic)

        result = await build_my_workspaces(self.db, user=staff)
        [item] = result["workspaces"]
        self.assertEqual((item.access_source, item.role, item.dso_name), ("clinic_assignment", "staff", dso.name))
        self.assertEqual((result["default_workspace"].scope_type, result["default_workspace"].clinic_id),
                         ("clinic", clinic.id))


class WalletAccessThenServiceTests(ServiceTestCase):
    """Endpoints run the wallet access check, then a service, on one session."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        from app.models.entities import Wallet, WalletStatus, WalletType
        self.Wallet = Wallet
        wallet = Wallet(id=uuid4(), clinic_id=uuid4(), wallet_type=WalletType.CLINIC, status=WalletStatus.ACTIVE,
                        currency="USD", cached_balance_minor=0, external_wallet_address="0xabc", kyc_verified=False)
        self.db.add(wallet)
        await self.db.commit()
        self.wallet_id = wallet.id
        self.db.expunge_all()

    async def kyc_status_after_access_check(self, verified):
        from app.security.wallet_access import get_wallet_for_access_check
        from app.services.billing.kyc_service import ToroForgeKYCService
        checked = await get_wallet_for_access_check(db=self.db, wallet_id=self.wallet_id)  # the endpoint keeps it
        service = ToroForgeKYCService(self.db, SimpleNamespace(
            check_address_verified=AsyncMock(return_value={"verified": verified})))
        result = await service.check_wallet_kyc_status(wallet_id=self.wallet_id)
        return checked, result

    async def test_refreshing_kyc_status_works_when_the_provider_changes_it(self):
        # Regression: /kyc-status?refresh=true failed with MissingGreenlet on Render.
        _, (status, verified) = await self.kyc_status_after_access_check(True)
        self.assertEqual((status["verified"], verified), (True, True))
        self.assertTrue(await self.db.scalar(select(self.Wallet.kyc_verified).where(self.Wallet.id == self.wallet_id)))

    async def test_refreshing_kyc_status_works_when_nothing_changes(self):
        _, (_, verified) = await self.kyc_status_after_access_check(False)
        self.assertFalse(verified)

    async def test_the_access_check_loads_the_whole_wallet(self):
        from sqlalchemy import inspect
        checked, _ = await self.kyc_status_after_access_check(True)
        await self.db.refresh(checked)
        self.assertEqual(inspect(checked).unloaded, set())
        self.assertEqual(checked.currency, "USD")  # readable without a lazy load


class AuthenticationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()
        self.user = Users(id=uuid4(), username="owner", email="owner@example.com", password=hashpassword("correct horse"))
        self.db.add(self.user)
        await self.db.commit()
        prefix = "app.services.auth.authentication."
        self.attempts = patch(prefix + "get_redis_attempts", AsyncMock(return_value=0))
        self.clear = patch(prefix + "clear_attempts_with_key", AsyncMock())
        self.increment = patch("app.services.auth.login_attempts.increment_attempts_with_key", AsyncMock(return_value=1))
        self.attempts_mock, self.clear_mock, self.increment_mock = self.attempts.start(), self.clear.start(), self.increment.start()

    async def asyncTearDown(self):
        patch.stopall()
        await close_local_session(self.db, self.engine)

    async def login(self, password, email=" Owner@Example.com "):
        return await authenticate_with_password(self.db, email=email, password=password, client_ip="127.0.0.1")

    async def test_correct_password_returns_the_user_and_clears_failed_attempts(self):
        self.assertEqual((await self.login("correct horse")).id, self.user.id)
        self.clear_mock.assert_awaited_once()
        self.increment_mock.assert_not_awaited()

    async def test_wrong_password_and_unknown_email_count_as_failures(self):
        for email in ("owner@example.com", "nobody@example.com"):
            with self.subTest(email=email), self.assertRaisesRegex(AuthenticationFailed, "Invalid Email or Password"):
                await self.login("wrong", email=email)
        self.assertEqual(self.increment_mock.await_count, 2)
        self.increment_mock.return_value = 5
        with self.assertRaises(TooManyRequests):
            await self.login("wrong")

    async def test_deactivated_account_is_refused_even_with_the_right_password(self):
        self.user.is_active = False
        await self.db.commit()
        with self.assertRaisesRegex(AuthenticationFailed, "Account is deactivated"):
            await self.login("correct horse")
        self.clear_mock.assert_not_awaited()

    async def test_password_check_runs_in_a_worker_thread(self):
        calls = []
        with patch("app.services.auth.authentication.verify_password", record_thread(calls, result=True)):
            await self.login("correct horse")
        self.assertEqual(calls, ["worker thread"])

    async def test_lockout_is_checked_before_the_database(self):
        self.attempts_mock.return_value = 5
        with patch.object(self.db, "scalar") as query, self.assertRaises(TooManyRequests):
            await self.login("correct horse")
        query.assert_not_called()

    async def test_google_login_needs_a_registered_active_account(self):
        with self.assertRaisesRegex(NotFound, "No Google account found"):
            await authenticate_with_google(self.db, google_user={"sub": "google-1", "email": "owner@example.com"})
        self.user.google_sub = "google-1"
        await self.db.commit()
        user = await authenticate_with_google(self.db, google_user={"sub": "google-1", "email": "x"})
        self.assertEqual(user.id, self.user.id)


class AppClientTestCase(unittest.TestCase):
    """The real application with a mocked database session and rate limiter."""

    def setUp(self):
        from main import app
        from app.infrastructure.database import get_db
        from app.security.oauth2 import get_current_user
        self.app, self.db = app, fake_async_session()
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=uuid4(), email="new@example.com")
        self.rate_limit = patch("app.infrastructure.middleware.async_redis", SimpleNamespace(eval=AsyncMock(return_value=1)))
        self.rate_limit.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.rate_limit.stop()


class HttpTranslationTests(AppClientTestCase):
    """Service errors reach the client with the same status and body as before."""

    def test_unknown_invite_token_is_404_with_detail(self):
        self.db.scalar.return_value = None
        response = self.client.post("/invites/accept", json={"token": "unknown"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Invalid invite token"})

    def test_failed_login_is_401_with_detail(self):
        self.db.scalar.return_value = None
        redis = SimpleNamespace(get=AsyncMock(return_value=None), eval=AsyncMock(return_value=1))
        with patch("app.services.auth.login_attempts.async_redis", redis):
            response = self.client.post("/login", json={"email": "nobody@example.com", "password": "wrong-password"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid Email or Password "})


class PasswordHashingOffEventLoopTests(AppClientTestCase):
    """bcrypt costs ~0.25 s of CPU; on the event loop it would freeze every other request."""

    def test_sign_up_hashes_the_password_in_a_worker_thread(self):
        calls = []
        self.db.scalar.return_value = None
        self.db.refresh.side_effect = lambda user: setattr(user, "id", uuid4())
        with patch("app.services.registration.hashpassword", record_thread(calls, result="hashed")):
            response = self.client.post("/register", json={"username": "new", "email": "new@example.com",
                                                           "password": "long-enough-password"})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(calls, ["worker thread"])


if __name__ == "__main__":
    unittest.main()
