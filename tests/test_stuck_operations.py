"""Resolving outside actions whose outcome is unknown (SQLite and fake providers only)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment, guarded_local_services
configure_test_environment()

from sqlalchemy import select

from local_database import close_local_session, local_session
from app.models.entities import (
    Audit_logs, ExternalOperation, LedgerStatus, Patients, PaymentProvider, PaymentTransaction, PaymentTransactionStatus,
    RegisteredClinics, Wallet, WalletLedgerEntry, WalletStatus, WalletTransfer, WalletTransferStatus, WalletType,
)
from app.schemas.api import AppointmentRequest
from app.security.encryption import decode_json_secret, decode_secret, encrypt_json_secret, encrypt_secret
from app.services import reconciliation_service as reconciliation
from app.services.appointment_service import AppointmentService, OpenDentalSyncError
from app.services.billing.funding_service import ToroForgeFundingService
from app.services.billing.wallet_transfer_service import ToroForgeWalletTransferService
from app.services.errors import Conflict, InvalidRequest, NotFound
from app.services.external_operations import ExternalOperations, ReconciliationRequired, stored_request
from app.services.reconciliation_checks import ProviderChecks

_guard = None


def setUpModule():
    global _guard
    _guard = guarded_local_services()
    _guard.__enter__()


def tearDownModule():
    _guard.__exit__(None, None, None)


def clinic_row(**values):
    return RegisteredClinics(id=uuid4(), crm_type="ghl", clinic_name="Test", clinic_number=1, clinic_timezone="UTC",
                             od_developer_key=encrypt_secret("key"), od_customer_key=encrypt_secret("key"),
                             crm_api_key=encrypt_secret("key"), location_id="location",
                             calendar_id=encrypt_secret("calendar"), owner_id=uuid4(), **values)


class ReconciliationTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()
        self.caches = [patch(f"app.services.{module}.invalidate_billing_scope")
                       for module in ("billing.wallet_transfer_service", "billing.funding_service", "reconciliation_service")]
        for cache in self.caches:
            cache.start()
        self.fake_providers_checks = self.fake_providers()

    async def asyncTearDown(self):
        for cache in self.caches:
            cache.stop()
        await close_local_session(self.db, self.engine)

    def fake_providers(self):
        """Read-only provider clients for the automatic checks; tests set what they answer."""
        self.toronet = AsyncMock(return_value=[])
        self.fiat = AsyncMock(return_value=[])
        self.tns_lookup = AsyncMock(return_value=None)
        self.verify_key = AsyncMock(return_value=False)
        self.opendental = SimpleNamespace(search_patients=AsyncMock(return_value=[]),
                                          get_appointments_in_operatory=AsyncMock(return_value=[]))
        return ProviderChecks(
            self.db, transfers=SimpleNamespace(get_dollar_transactions=self.toronet),
            payments=SimpleNamespace(get_payments_for_address=self.fiat,
                                     client=SimpleNamespace(config=SimpleNamespace(admin="admin", adminpwd="pwd"))),
            tns=SimpleNamespace(get_address_for_name=self.tns_lookup), keystore=SimpleNamespace(verify_key=self.verify_key),
            opendental_for=lambda clinic: self.opendental)

    async def auto(self, **options):
        return await reconciliation.auto_resolve(self.db, self.fake_providers_checks, **options)

    async def make_old(self, row, by=timedelta(hours=1)):
        # Pretend the row was created a while ago, past the "may still be running" window.
        row.created_at = datetime.now(timezone.utc) - by
        await self.db.commit()

    async def audit_actions(self):
        return [row.action for row in (await self.db.scalars(select(Audit_logs))).all()]


class TransferTestCase(ReconciliationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.dso_id = uuid4()
        clinic = clinic_row(dso_id=self.dso_id)
        self.clinic_id = clinic.id
        self.sender = Wallet(id=uuid4(), dso_id=self.dso_id, wallet_type=WalletType.DSO_TREASURY, status=WalletStatus.ACTIVE,
                             currency="USD", cached_balance_minor=10000, external_wallet_address="0xsender",
                             external_wallet_password_encrypted=encrypt_secret("password"))
        self.receiver = Wallet(id=uuid4(), clinic_id=self.clinic_id, dso_id=self.dso_id, wallet_type=WalletType.CLINIC,
                               status=WalletStatus.ACTIVE, currency="USD", cached_balance_minor=0,
                               external_wallet_address="0xreceiver")
        self.db.add_all([clinic, self.sender, self.receiver])
        await self.db.commit()
        self.client = SimpleNamespace(make_inter_wallet_transfer=AsyncMock(side_effect=TimeoutError("no answer")))
        self.service = ToroForgeWalletTransferService(self.db, self.client)

    async def send(self, key="first"):
        return await self.service.transfer_from_dso_to_clinic(dso_id=self.dso_id, clinic_id=self.clinic_id, amount="25",
                                                              currency="USD", idempotency_key=key,
                                                              initiated_by_user_id=uuid4())

    async def stuck_transfer(self):
        with self.assertRaises(TimeoutError):
            await self.send()
        [item] = await reconciliation.list_unknown_outcomes(self.db)
        return item

    async def balances(self):
        await self.db.refresh(self.sender)
        await self.db.refresh(self.receiver)
        return self.sender.cached_balance_minor, self.receiver.cached_balance_minor

    def toroforge_answers(self, txid):
        self.client.make_inter_wallet_transfer.side_effect = None
        self.client.make_inter_wallet_transfer.return_value = {"result": True, "data": [{"TX_ID": txid}]}


class TransferResolutionTests(TransferTestCase):
    async def test_listing_shows_what_to_look_for_and_the_dso_stays_blocked(self):
        item = await self.stuck_transfer()
        self.assertEqual((item.kind, item.action, item.state), ("operation", "wallet_transfer", "uncertain"))
        self.assertEqual(item.context["amount"], "25.00 USD")
        self.assertEqual((item.context["from"], item.context["to"]), ("0xsender", "0xreceiver"))
        self.assertFalse(item.may_still_be_running)
        self.assertIn("ToroForge", item.where_to_check)
        with self.assertRaises(ReconciliationRequired):
            await self.send(key="second")

    async def test_money_that_moved_is_posted_once_and_the_dso_can_send_again(self):
        item = await self.stuck_transfer()
        summary = await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi",
                                               note="seen in ToroForge", fields={"TX_ID": "TX-CONFIRMED"})
        self.assertIn("completed", summary)
        self.assertEqual(await self.balances(), (7500, 2500))
        transfer = (await self.db.scalars(select(WalletTransfer))).one()
        self.assertEqual((transfer.status, transfer.external_transaction_id),
                         (WalletTransferStatus.COMPLETED, "TX-CONFIRMED"))
        self.assertEqual((await self.db.scalars(select(ExternalOperation))).one().state, "applied")
        self.assertIn("operator_resolved_happened", await self.audit_actions())

        self.toroforge_answers("TX-NEXT")
        self.assertEqual((await self.send(key="second"))["status"], "completed")
        self.assertEqual(self.client.make_inter_wallet_transfer.await_count, 2)  # the resolved one was never re-sent
        self.assertEqual(await self.balances(), (5000, 5000))

    async def test_money_that_did_not_move_is_closed_and_a_new_transfer_works(self):
        item = await self.stuck_transfer()
        summary = await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi",
                                               note="no transaction in ToroForge")
        self.assertIn("not sent", summary)
        self.assertEqual(await self.balances(), (10000, 0))
        self.assertEqual((await self.db.scalars(select(WalletTransfer))).one().status, WalletTransferStatus.FAILED)
        self.assertEqual({entry.status for entry in (await self.db.scalars(select(WalletLedgerEntry))).all()},
                         {LedgerStatus.FAILED})
        self.assertEqual(await reconciliation.list_unknown_outcomes(self.db), [])
        self.assertIn("operator_resolved_not_done", await self.audit_actions())

        self.toroforge_answers("TX-RETRY")
        self.assertEqual((await self.send(key="retry"))["status"], "completed")
        self.assertEqual(await self.balances(), (7500, 2500))

    async def test_a_confirmed_transfer_needs_its_transaction_id_and_cannot_be_resolved_twice(self):
        item = await self.stuck_transfer()
        with self.assertRaisesRegex(InvalidRequest, "TX_ID"):
            await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi")
        await self.db.rollback()
        await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi")
        with self.assertRaisesRegex(Conflict, "already"):
            await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi", fields={"TX_ID": "X"})


class AppointmentTestCase(ReconciliationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.clinic = clinic_row()
        self.db.add(self.clinic)
        await self.db.commit()
        self.provider = SimpleNamespace(create_appointments=AsyncMock(side_effect=TimeoutError("no answer")))
        self.service = AppointmentService(self.db, self.clinic, self.provider)
        self.req = AppointmentRequest(date_str="2026-10-17", start_str="09:00", end_str="09:30", status="Scheduled",
                                      calendar_id="calendar", event_id="event", contact_id="contact", pat_id=uuid4(),
                                      pat_Num=12, clinic_timezone="UTC", commslog="", pop_up="")

    async def create(self):
        return await self.service.create_appointment(self.req, "XXXXXX", 1, datetime(2026, 10, 17, 9))

    async def stuck_appointment(self):
        with self.assertRaises(OpenDentalSyncError):
            await self.create()
        [item] = await reconciliation.list_unknown_outcomes(self.db)
        return item


class AppointmentResolutionTests(AppointmentTestCase):
    async def test_appointment_found_in_opendental_is_reused_not_booked_again(self):
        item = await self.stuck_appointment()
        self.assertEqual((item.action, item.context["clinic"]), ("appointment_create", str(self.clinic.id)))
        self.assertIn("OpenDental", item.where_to_check)
        await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi", fields={"AptNum": "42"})
        self.assertEqual(await self.create(), {"AptNum": 42})
        self.provider.create_appointments.assert_awaited_once()

    async def test_appointment_missing_in_opendental_is_booked_on_the_next_attempt(self):
        item = await self.stuck_appointment()
        await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi")
        self.provider.create_appointments.side_effect = None
        self.provider.create_appointments.return_value = {"AptNum": 77}
        self.assertEqual(await self.create(), {"AptNum": 77})
        self.assertEqual(self.provider.create_appointments.await_count, 2)

    async def test_appointment_numbers_must_be_whole_numbers(self):
        item = await self.stuck_appointment()
        with self.assertRaisesRegex(InvalidRequest, "whole numbers"):
            await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi", fields={"AptNum": "abc"})


class FundingTestCase(ReconciliationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.wallet = Wallet(id=uuid4(), clinic_id=uuid4(), wallet_type=WalletType.CLINIC, status=WalletStatus.ACTIVE,
                             external_wallet_address="0xabc", kyc_verified=True, currency="USD", cached_balance_minor=0)
        self.db.add(self.wallet)
        await self.db.commit()
        self.client = SimpleNamespace(initialize_payment=AsyncMock(side_effect=TimeoutError("no answer")),
                                      client=SimpleNamespace(config=SimpleNamespace(admin="test", adminpwd="test")))
        self.service = ToroForgeFundingService(self.db, self.client)

    async def initialize(self, key="first"):
        return await self.service.initialize_wallet_funding(
            wallet_id=self.wallet.id, request_id=key, amount="10", currency="USD", payment_type="card",
            success_url="https://example.com/ok", cancel_url="https://example.com/no")

    async def stuck_funding(self, *, old=True):
        with self.assertRaises(TimeoutError):
            await self.initialize()
        [item] = await reconciliation.list_unknown_outcomes(self.db)
        if old:
            await self.make_old(await self.db.get(PaymentTransaction, item.id))
        return item


class FundingStartResolutionTests(FundingTestCase):
    async def test_a_fresh_funding_start_is_left_alone_unless_forced(self):
        item = await self.stuck_funding(old=False)
        self.assertTrue(item.may_still_be_running)
        with self.assertRaisesRegex(Conflict, "may still be running"):
            await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi")
        await self.db.rollback()
        await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi", force=True)
        self.assertEqual(await reconciliation.list_unknown_outcomes(self.db), [])

    async def test_a_funding_start_without_a_link_is_closed_and_a_new_top_up_allowed(self):
        item = await self.stuck_funding()
        self.assertEqual(item.context["amount"], "10.00 USD")
        summary = await reconciliation.resolve(self.db, item.id, happened=False, resolved_by="Funmi")
        self.assertIn("new top-up", summary)
        payment = await self.db.get(PaymentTransaction, item.id)
        self.assertEqual(payment.status, PaymentTransactionStatus.FAILED)
        self.assertEqual((await self.db.scalars(select(WalletLedgerEntry))).one().status, LedgerStatus.FAILED)

        self.client.initialize_payment.side_effect = None
        self.client.initialize_payment.return_value = {"result": True, "data": [{"TX_ID": "TX-NEW"}]}
        self.assertEqual((await self.initialize(key="second"))["status"], "pending")
        self.assertEqual(self.client.initialize_payment.await_count, 2)

    async def test_a_payment_found_at_toroforge_is_linked_but_never_credited_here(self):
        item = await self.stuck_funding()
        summary = await reconciliation.resolve(self.db, item.id, happened=True, resolved_by="Funmi",
                                               fields={"TX_ID": "TX-FOUND"})
        self.assertIn("verify the deposit", summary)
        payment = await self.db.get(PaymentTransaction, item.id)
        self.assertEqual((payment.status, payment.external_payment_id), (PaymentTransactionStatus.PENDING, "TX-FOUND"))
        await self.db.refresh(self.wallet)
        self.assertEqual(self.wallet.cached_balance_minor, 0)
        self.assertEqual(await reconciliation.list_unknown_outcomes(self.db), [])


class GuardTests(ReconciliationTestCase):
    async def in_flight(self, *, old):
        operation = ExternalOperation(id=uuid4(), operation_key=f"commslog:{uuid4()}:hash", resource_id=uuid4(),
                                      action="commslog", request_hash="hash", state="in_flight")
        self.db.add(operation)
        await self.db.commit()
        if old:
            await self.make_old(operation)
        return operation

    async def test_finished_operations_are_not_listed(self):
        for state in ("succeeded", "applied", "failed"):
            self.db.add(ExternalOperation(id=uuid4(), operation_key=f"popup:{uuid4()}:{state}", resource_id=uuid4(),
                                          action="popup", request_hash="hash", state=state))
        await self.db.commit()
        self.assertEqual(await reconciliation.list_unknown_outcomes(self.db), [])

    async def test_a_call_that_may_still_be_running_needs_force(self):
        operation_id = (await self.in_flight(old=False)).id  # the refusal's rollback expires the row
        [item] = await reconciliation.list_unknown_outcomes(self.db)
        self.assertTrue(item.may_still_be_running)
        with self.assertRaisesRegex(Conflict, "may still be running"):
            await reconciliation.resolve(self.db, operation_id, happened=True, resolved_by="Funmi")
        await self.db.rollback()
        summary = await reconciliation.resolve(self.db, operation_id, happened=True, resolved_by="Funmi", force=True)
        self.assertIn("reuses the result", summary)

    async def test_an_old_crashed_call_resolves_without_force(self):
        operation = await self.in_flight(old=True)
        await reconciliation.resolve(self.db, operation.id, happened=False, resolved_by="Funmi")
        self.assertEqual(await reconciliation.list_unknown_outcomes(self.db), [])

    async def test_unknown_ids_and_anonymous_resolutions_are_refused(self):
        with self.assertRaises(NotFound):
            await reconciliation.resolve(self.db, uuid4(), happened=False, resolved_by="Funmi")
        operation = await self.in_flight(old=True)
        with self.assertRaisesRegex(InvalidRequest, "who checked"):
            await reconciliation.resolve(self.db, operation.id, happened=False, resolved_by="  ")


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------------- automatic checks

def toronet_record(tx_hash, value, at, *, sender="0xsender", receiver="0xreceiver"):
    return {"EV_Hash": tx_hash, "EV_Contract": "dollarclientContract", "EV_Event": "Transfer", "EV_From": sender,
            "EV_To": receiver, "EV_Value": value, "EV_Time": at.strftime("%Y-%m-%dT%H:%M:%S.000Z")}


class AutomaticTransferTests(TransferTestCase):
    async def stuck_for(self, age):
        item = await self.stuck_transfer()
        await self.make_old(await self.db.get(ExternalOperation, item.id), by=age)
        await self.make_old((await self.db.scalars(select(WalletTransfer))).one(), by=age)
        self.started = datetime.now(timezone.utc) - age
        return item

    async def transfer_status(self):
        return (await self.db.scalars(select(WalletTransfer))).one().status

    async def test_a_transfer_toronet_shows_once_is_posted_automatically(self):
        await self.stuck_for(timedelta(minutes=30))
        self.toronet.return_value = [toronet_record("0xhash", 25.0, self.started + timedelta(seconds=3)),
                                     toronet_record("0xolder", 25.0, self.started - timedelta(days=1))]
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        self.assertEqual(await self.balances(), (7500, 2500))
        transfer = (await self.db.scalars(select(WalletTransfer))).one()
        self.assertEqual((transfer.status, transfer.external_transaction_id), (WalletTransferStatus.COMPLETED, "0xhash"))
        self.toronet.assert_awaited_once_with(address="0xsender", count=100)
        audit = (await self.db.scalars(select(Audit_logs).where(Audit_logs.action == "operator_resolved_happened"))).one()
        details = decode_json_secret(audit.details["encrypted"])
        self.assertEqual(details["resolved_by"], "automatic check")
        self.assertIn("0xhash", details["note"])

    async def test_a_transfer_missing_from_toronet_is_closed_only_after_an_hour(self):
        await self.stuck_for(timedelta(minutes=30))
        [result] = await self.auto()
        self.assertEqual(result.outcome, "waiting")
        self.assertEqual(await self.transfer_status(), WalletTransferStatus.PENDING)

        await self.stuck_for_longer()
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        self.assertEqual(await self.transfer_status(), WalletTransferStatus.FAILED)
        self.assertEqual(await self.balances(), (10000, 0))

    async def stuck_for_longer(self):
        for row in ((await self.db.scalars(select(ExternalOperation))).one(), (await self.db.scalars(select(WalletTransfer))).one()):
            await self.make_old(row, by=timedelta(hours=2))

    async def test_near_matches_and_duplicates_are_left_for_a_person(self):
        await self.stuck_for(timedelta(hours=2))
        soon = self.started + timedelta(seconds=5)
        cases = {
            "different amount": [toronet_record("0xa", 24.99, soon)],
            "two matches": [toronet_record("0xa", 25.0, soon), toronet_record("0xb", 25, soon)],
            "fractional cents": [toronet_record("0xa", 25.004, soon)],
            "unreadable time": [toronet_record("0xa", 25.0, soon) | {"EV_Time": "yesterday"}],
            "history cut off": [toronet_record(f"0x{n}", 1.0, soon, receiver="0xother") for n in range(100)],
        }
        for case, records in cases.items():
            with self.subTest(case):
                self.toronet.return_value = records
                [result] = await self.auto()
                self.assertEqual(result.outcome, "needs a person")
                self.assertEqual(await self.transfer_status(), WalletTransferStatus.PENDING)

    async def test_a_transaction_already_used_by_another_transfer_is_not_reused(self):
        await self.stuck_for(timedelta(hours=2))
        self.db.add(WalletTransfer(id=uuid4(), from_wallet_id=self.sender.id, to_wallet_id=self.receiver.id,
                                   amount_minor=2500, currency="USD", status=WalletTransferStatus.COMPLETED,
                                   external_transaction_id="0xused", idempotency_key="earlier"))
        await self.db.commit()
        self.toronet.return_value = [toronet_record("0xused", 25.0, self.started + timedelta(seconds=1))]
        [result] = await self.auto()
        self.assertEqual(result.outcome, "needs a person")

    async def test_dry_run_changes_nothing(self):
        await self.stuck_for(timedelta(hours=2))
        self.toronet.return_value = [toronet_record("0xhash", 25.0, self.started + timedelta(seconds=3))]
        [result] = await self.auto(dry_run=True)
        self.assertEqual(result.outcome, "would resolve")
        self.assertEqual(await self.transfer_status(), WalletTransferStatus.PENDING)
        self.assertNotIn("operator_resolved_happened", await self.audit_actions())

    async def test_a_provider_error_is_reported_and_retried_next_run(self):
        await self.stuck_for(timedelta(hours=2))
        self.toronet.side_effect = TimeoutError("Toronet is down")
        [result] = await self.auto()
        self.assertEqual(result.outcome, "error")
        self.assertIn("TimeoutError", result.summary)
        self.assertEqual(await self.transfer_status(), WalletTransferStatus.PENDING)

    async def test_the_request_is_kept_encrypted_while_the_outcome_is_unknown(self):
        item = await self.stuck_transfer()
        operation = await self.db.get(ExternalOperation, item.id)
        self.assertEqual(stored_request(operation), {"to": str(self.receiver.id), "amount_minor": 2500, "currency": "USD"})
        self.assertNotIn("2500", operation.request_encrypted)


class AutomaticFundingTests(FundingTestCase):
    def payment_record(self, txid, amount="10"):
        return {"TX_ID": txid, "TX_Address": "0xABC", "TX_Currency": "USD", "TX_Amount": amount}

    async def test_the_one_unlinked_payment_is_linked_but_not_credited(self):
        item = await self.stuck_funding()
        self.db.add(PaymentTransaction(id=uuid4(), wallet_id=self.wallet.id, provider=PaymentProvider.TOROFORGE,
                                       purpose="wallet_funding", amount_minor=1000, currency="USD",
                                       status=PaymentTransactionStatus.SUCCEEDED, external_payment_id="TX-OTHER",
                                       idempotency_key="other"))
        await self.db.commit()
        self.fiat.return_value = [self.payment_record("TX-OTHER"), self.payment_record("TX-BIGGER", "11"),
                                  self.payment_record("TX-FOUND")]
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        payment = await self.db.get(PaymentTransaction, item.id)
        self.assertEqual((payment.status, payment.external_payment_id), (PaymentTransactionStatus.PENDING, "TX-FOUND"))
        await self.db.refresh(self.wallet)
        self.assertEqual(self.wallet.cached_balance_minor, 0)
        self.assertEqual(self.fiat.await_args.kwargs["address"], "0xabc")

    async def test_no_payment_after_an_hour_closes_the_funding_start(self):
        item = await self.stuck_funding()
        await self.make_old(await self.db.get(PaymentTransaction, item.id), by=timedelta(hours=2))
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        self.assertEqual((await self.db.get(PaymentTransaction, item.id)).status, PaymentTransactionStatus.FAILED)

    async def test_two_unlinked_payments_are_left_for_a_person(self):
        await self.stuck_funding()
        self.fiat.return_value = [self.payment_record("TX-1"), self.payment_record("TX-2")]
        [result] = await self.auto()
        self.assertEqual(result.outcome, "needs a person")


class AutomaticWalletTests(ReconciliationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.wallet = Wallet(id=uuid4(), clinic_id=uuid4(), wallet_type=WalletType.CLINIC, status=WalletStatus.ACTIVE,
                             currency="USD", cached_balance_minor=0, external_wallet_username="smile-clinic",
                             external_wallet_address="0xwallet", external_wallet_password_encrypted=encrypt_secret("old"))
        self.db.add(self.wallet)
        await self.db.commit()

    async def stuck(self, action, key, *, age=timedelta(hours=2), secrets=None):
        operation = ExternalOperation(id=uuid4(), operation_key=f"{key}:{self.wallet.id}", resource_id=self.wallet.id,
                                      action=action, request_hash="hash", state="uncertain",
                                      secrets_encrypted=encrypt_json_secret(secrets))
        self.db.add(operation)
        await self.db.commit()
        await self.make_old(operation, by=age)
        return operation.id

    async def state(self, operation_id):
        return (await self.db.get(ExternalOperation, operation_id, populate_existing=True)).state

    async def test_a_lost_key_creation_is_closed_after_an_hour_so_creation_can_restart(self):
        self.wallet.external_wallet_address = None
        await self.db.commit()
        operation_id = await self.stuck("wallet_keystore", "wallet-keystore", age=timedelta(minutes=30))
        self.assertEqual([result.outcome for result in await self.auto()], ["waiting"])
        await self.make_old(await self.db.get(ExternalOperation, operation_id), by=timedelta(hours=2))
        self.assertEqual([result.outcome for result in await self.auto()], ["resolved"])
        self.assertEqual(await self.state(operation_id), "failed")

    async def test_a_name_registered_to_this_wallet_counts_as_done(self):
        operation_id = await self.stuck("wallet_tns", "wallet-tns")
        self.tns_lookup.return_value = "0xWALLET"
        self.assertEqual([result.outcome for result in await self.auto()], ["resolved"])
        self.assertEqual(await self.state(operation_id), "succeeded")
        self.tns_lookup.assert_awaited_once_with(username="smile-clinic")

    async def test_a_name_registered_to_someone_else_needs_a_person(self):
        operation_id = await self.stuck("wallet_tns", "wallet-tns")
        self.tns_lookup.return_value = "0xsomeoneelse"
        self.assertEqual([result.outcome for result in await self.auto()], ["needs a person"])
        self.assertEqual(await self.state(operation_id), "uncertain")

    async def test_a_password_change_toronet_accepts_is_applied_to_the_wallet(self):
        operation_id = await self.stuck("wallet_password_rotation", "wallet-password", secrets={"new_password": "new"})
        self.verify_key.return_value = True
        self.assertEqual([result.outcome for result in await self.auto()], ["resolved"])
        self.verify_key.assert_awaited_once_with(address="0xwallet", password="new")
        await self.db.refresh(self.wallet)
        self.assertEqual(decode_secret(self.wallet.external_wallet_password_encrypted), "new")
        self.assertEqual(await self.state(operation_id), "applied")

    async def test_a_password_change_toronet_rejects_is_never_guessed(self):
        operation_id = await self.stuck("wallet_password_rotation", "wallet-password", secrets={"new_password": "new"})
        self.assertEqual([result.outcome for result in await self.auto()], ["needs a person"])
        await self.db.refresh(self.wallet)
        self.assertEqual(decode_secret(self.wallet.external_wallet_password_encrypted), "old")
        self.assertEqual(await self.state(operation_id), "uncertain")

    async def test_comm_logs_and_popups_are_left_for_a_person(self):
        self.db.add(ExternalOperation(id=uuid4(), operation_key=f"commslog:{uuid4()}:hash", resource_id=uuid4(),
                                      action="commslog", request_hash="hash", state="uncertain"))
        await self.db.commit()
        await self.make_old((await self.db.scalars(select(ExternalOperation))).one(), by=timedelta(hours=2))
        [result] = await self.auto()
        self.assertEqual(result.outcome, "needs a person")


class AutomaticAppointmentTests(AppointmentTestCase):
    async def test_an_appointment_found_in_opendental_is_reused(self):
        item = await self.stuck_appointment()
        await self.make_old(await self.db.get(ExternalOperation, item.id), by=timedelta(minutes=20))
        self.opendental.get_appointments_in_operatory.return_value = [
            {"AptNum": 41, "PatNum": 99, "AptDateTime": "2026-10-17 09:00:00"},
            {"AptNum": 42, "PatNum": 12, "AptDateTime": "2026-10-17 09:00:00"},
            {"AptNum": 43, "PatNum": 12, "AptDateTime": "2026-10-17 11:00:00"},
        ]
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        self.opendental.get_appointments_in_operatory.assert_awaited_once_with("1", "2026-10-17", "2026-10-17")
        self.assertEqual(await self.create(), {"AptNum": 42})
        self.provider.create_appointments.assert_awaited_once()  # never booked twice

    async def test_an_appointment_missing_after_an_hour_is_booked_on_the_next_attempt(self):
        item = await self.stuck_appointment()
        await self.make_old(await self.db.get(ExternalOperation, item.id), by=timedelta(hours=2))
        [result] = await self.auto()
        self.assertEqual(result.outcome, "resolved")
        self.provider.create_appointments.side_effect = None
        self.provider.create_appointments.return_value = {"AptNum": 77}
        self.assertEqual(await self.create(), {"AptNum": 77})


class AutomaticPatientTests(ReconciliationTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        clinic = clinic_row()
        self.patient = Patients(id=uuid4(), FName="Ada", LName="Lovelace", Gender="F", phone="555", contact_id="c1",
                                clinic_id=clinic.id)
        self.db.add_all([clinic, self.patient])
        await self.db.commit()
        self.patient_id = self.patient.id
        with self.assertRaises(TimeoutError):
            await ExternalOperations(self.db).execute(
                key=f"patient-create:{self.patient_id}", resource_id=self.patient_id, action="patient_create",
                request={"FName": "Ada", "LName": "Lovelace", "Birthdate": "1990-01-02"},
                call=AsyncMock(side_effect=TimeoutError("no answer")))
        self.operation = (await self.db.scalars(select(ExternalOperation))).one()
        await self.make_old(self.operation, by=timedelta(hours=2))

    async def result_of_auto(self):
        [result] = await self.auto()
        operation = await self.db.get(ExternalOperation, self.operation.id, populate_existing=True)
        return result.outcome, operation.state

    async def test_the_patient_found_in_opendental_is_reused(self):
        self.opendental.search_patients.return_value = [
            {"PatNum": 9, "FName": " ada ", "LName": "LOVELACE", "Birthdate": "1990-01-02T00:00:00"}]
        self.assertEqual(await self.result_of_auto(), ("resolved", "succeeded"))
        self.opendental.search_patients.assert_awaited_once_with(last_name="Lovelace", date_of_birth="1990-01-02")

    async def test_a_patient_linked_by_a_later_attempt_needs_no_search(self):
        (await self.db.get(Patients, self.patient_id)).pat_num = 9
        await self.db.commit()
        self.assertEqual(await self.result_of_auto(), ("resolved", "succeeded"))
        self.opendental.search_patients.assert_not_awaited()

    async def test_similar_patients_are_left_for_a_person(self):
        self.opendental.search_patients.return_value = [
            {"PatNum": 9, "FName": "Adaline", "LName": "Lovelace", "Birthdate": "1990-01-02"}]
        self.assertEqual(await self.result_of_auto(), ("needs a person", "uncertain"))

    async def test_no_patient_after_an_hour_allows_a_fresh_attempt(self):
        self.assertEqual(await self.result_of_auto(), ("resolved", "failed"))

    async def test_patient_details_are_encrypted_and_dropped_once_applied(self):
        self.assertNotIn("Lovelace", self.operation.request_encrypted)
        self.opendental.search_patients.return_value = [
            {"PatNum": 9, "FName": "Ada", "LName": "Lovelace", "Birthdate": "1990-01-02"}]
        await self.auto()
        journal = ExternalOperations(self.db)
        await journal.mark_applied(self.operation.operation_key)
        await self.db.commit()
        operation = await self.db.get(ExternalOperation, self.operation.id, populate_existing=True)
        self.assertEqual((operation.state, operation.request_encrypted), ("applied", None))
