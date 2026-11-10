"""Billing safety regressions; all provider and database boundaries are fake."""
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from runtime_support import configure_test_environment, guarded_local_services
configure_test_environment()

from app.services.billing.money import coerce_amount_decimal, to_amount_minor, balance_amount_to_minor
from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
from app.services.billing.funding_service import ToroForgeFundingService
from app.services.external_operations import ExternalOperations, ReconciliationRequired, DefinitiveRejection
from app.models.entities import ExternalOperation, Wallet, WalletType, WalletStatus, RegisteredClinics, WalletTransfer, WalletTransferStatus
from app.security.encryption import encrypt_secret, decode_json_secret
from sqlalchemy import func, select
from local_database import close_local_session, fake_async_session, local_session

_guard = None
def setUpModule():
    global _guard
    _guard = guarded_local_services();_guard.__enter__()
def tearDownModule():
    _guard.__exit__(None,None,None)


class MoneySafetyTests(unittest.TestCase):
    def test_nonfinite_and_oversized_amounts_are_validation_errors(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity", "1e100", "0", "-1"):
            with self.subTest(value=value), self.assertRaises(ToroForgeValidationError):
                to_amount_minor(amount=coerce_amount_decimal(value), currency="USD")

    def test_fractional_cents_are_not_silently_rounded(self):
        with self.assertRaises(ToroForgeValidationError):
            to_amount_minor(amount=Decimal("1.005"), currency="USD")
        self.assertEqual(to_amount_minor(amount=Decimal("1.00"), currency="USD"), 100)

    def test_zero_balance_is_valid_but_nonfinite_balance_is_not(self):
        self.assertEqual(balance_amount_to_minor(amount="0", currency="USD"), 0)
        for amount in ("NaN", "Infinity", "-1"):
            with self.assertRaises(ToroForgeValidationError):
                balance_amount_to_minor(amount=amount, currency="USD")


class FundingSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = fake_async_session()
        self.provider = MagicMock()
        self.provider.initialize_payment = AsyncMock()
        self.service = ToroForgeFundingService(self.db, self.provider)
        self.payment = SimpleNamespace(currency="USD", amount_minor=10000, external_payment_id="TX1")
        self.wallet = SimpleNamespace(id=uuid4(), external_wallet_address="0xabc", currency="USD")

    async def test_currency_mismatch_is_rejected_before_provider(self):
        self.db.scalar.return_value = SimpleNamespace(
            id=self.wallet.id, currency="USD", status=__import__('app.models.entities',fromlist=['WalletStatus']).WalletStatus.ACTIVE,
            external_wallet_address="0xabc", kyc_verified=True)
        with self.assertRaises(ToroForgeValidationError):
            await self.service.initialize_wallet_funding(wallet_id=self.wallet.id, request_id="one", amount="100",
                currency="NGN", payment_type="card", success_url="https://example.com/ok", cancel_url="https://example.com/no")
        self.provider.initialize_payment.assert_not_awaited()

    async def test_incomplete_or_mismatched_record_never_matches(self):
        record = {"TX_ID":"TX1", "TX_Currency":"USD", "TX_Address":"0xabc", "TX_Amount":"100"}
        for bad in (None, record | {"TX_Amount":None}, record | {"TX_ID":"TX2"},
                    record | {"TX_Address":"0xother"}, record | {"TX_Currency":"NGN"}, record | {"TX_Amount":"101"}):
            self.assertIsNone(self.service.find_matching_fiat_transaction(records=[bad],payment=self.payment,
                              wallet=self.wallet,txid="TX1"))

    async def test_matching_deposit_needs_no_extra_status_field(self):
        record = {"TX_ID":"TX1", "TX_Currency":"USD", "TX_Address":"0xabc", "TX_Amount":"100"}
        self.assertEqual(self.service.find_matching_fiat_transaction(records=[record],payment=self.payment,
                         wallet=self.wallet,txid="TX1"),record)
        for key in record:
            bad = record.copy(); bad.pop(key)
            self.assertIsNone(self.service.find_matching_fiat_transaction(records=[bad],payment=self.payment,
                              wallet=self.wallet,txid="TX1"))


class ExternalOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()
        self.service = ExternalOperations(self.db)
        self.resource = uuid4()

    async def asyncTearDown(self):
        await close_local_session(self.db, self.engine)

    async def execute(self, call, key="operation", request=None):
        return await self.service.execute(key=key, resource_id=self.resource, action="test",
            request=request or {"amount":100}, call=call, secrets={"password":"private"})

    async def test_intent_committed_before_provider_and_result_reused(self):
        async def provider():
            self.assertEqual((await self.service.get("operation")).state, "in_flight")
            self.assertFalse(self.db.new)
            return {"result":True, "private_result":"sensitive"}
        call = AsyncMock(side_effect=provider)
        await self.execute(call)
        await self.execute(call)
        call.assert_awaited_once()
        row = await self.service.get("operation")
        self.assertNotIn("private", row.secrets_encrypted)
        self.assertNotIn("sensitive", row.result_encrypted)

    async def test_timeout_and_new_keys_do_not_replay(self):
        call = AsyncMock(side_effect=TimeoutError("uncertain"))
        with self.assertRaises(TimeoutError): await self.execute(call)
        self.assertEqual((await self.service.get("operation")).state, "uncertain")
        for key in ("operation", "another"):
            with self.assertRaises(ReconciliationRequired): await self.execute(call, key)
        call.assert_awaited_once()

    async def test_changed_request_cannot_reuse_key(self):
        call = AsyncMock(return_value={"ok":True})
        await self.execute(call)
        with self.assertRaises(ToroForgeValidationError): await self.execute(call, request={"amount":200})
        call.assert_awaited_once()

    async def test_explicit_rejection_allows_new_operation_but_not_old_key(self):
        with self.assertRaises(DefinitiveRejection):
            await self.execute(AsyncMock(side_effect=DefinitiveRejection("rejected")))
        self.assertEqual((await self.service.get("operation")).state, "failed")
        call = AsyncMock(return_value={"ok":True})
        await self.execute(call, key="another")
        call.assert_awaited_once()


class DurableTransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db, self.engine = await local_session()
        self.dso_id, self.clinic_id = uuid4(), uuid4()
        self.db.add(RegisteredClinics(id=self.clinic_id, crm_type="ghl", clinic_name="Test",clinic_number=1,
            clinic_timezone="UTC", od_developer_key="unused", od_customer_key="unused", crm_api_key="unused",
            location_id="location",calendar_id="calendar",owner_id=uuid4(),dso_id=self.dso_id))
        self.sender = Wallet(id=uuid4(),dso_id=self.dso_id,wallet_type=WalletType.DSO_TREASURY,status=WalletStatus.ACTIVE,
            currency="USD",cached_balance_minor=10000,external_wallet_address="0xsender",external_wallet_password_encrypted=encrypt_secret("password"))
        self.receiver = Wallet(id=uuid4(),clinic_id=self.clinic_id,dso_id=self.dso_id,wallet_type=WalletType.CLINIC,
            status=WalletStatus.ACTIVE,currency="USD",cached_balance_minor=0,external_wallet_address="0xreceiver")
        self.db.add_all([self.sender,self.receiver]); await self.db.commit()
        self.client = SimpleNamespace(make_inter_wallet_transfer=AsyncMock(return_value={"result":True,"data":[{"TX_ID":"TRANSFER1", "test_status":"settled"}]}))
        from app.config import settings
        self.settlement = patch.multiple(settings, toroforge_settlement_field="test_status",
                                        toroforge_settlement_success_values=["settled"])
        self.settlement.start()
        from app.services.billing.wallet_transfer_service import ToroForgeWalletTransferService
        self.service = ToroForgeWalletTransferService(self.db,self.client)
        self.cache = patch("app.services.billing.wallet_transfer_service.invalidate_billing_scope");self.cache.start()

    async def asyncTearDown(self):
        self.settlement.stop();self.cache.stop();await close_local_session(self.db,self.engine)

    async def send(self,key="one",amount="25"):
        return await self.service.transfer_from_dso_to_clinic(dso_id=self.dso_id,clinic_id=self.clinic_id,amount=amount,
            currency="USD",idempotency_key=key,initiated_by_user_id=uuid4())

    async def test_confirmed_transfer_posts_once_and_mismatch_is_rejected(self):
        result = await self.send()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.sender.cached_balance_minor,7500)
        self.assertEqual(self.receiver.cached_balance_minor,2500)
        self.assertTrue((await self.send())["reused"])
        with self.assertRaises(ToroForgeValidationError): await self.send(amount="30")
        self.client.make_inter_wallet_transfer.assert_awaited_once()

    async def test_uncertain_transfer_retains_intent_and_blocks_new_key(self):
        self.client.make_inter_wallet_transfer.side_effect = TimeoutError("unknown")
        with self.assertRaises(TimeoutError): await self.send()
        transfer = (await self.db.scalars(select(WalletTransfer))).one()
        self.assertEqual(transfer.status,WalletTransferStatus.PENDING)
        self.assertEqual((await self.send())["status"], "pending")
        with self.assertRaises(ReconciliationRequired): await self.send(key="different")
        self.client.make_inter_wallet_transfer.assert_awaited_once()

    async def test_local_failure_after_remote_success_can_resume_without_sending(self):
        original = self.service.post_confirmed_transfer
        with patch.object(self.service,"post_confirmed_transfer",side_effect=RuntimeError("local failure")):
            with self.assertRaises(RuntimeError): await self.send()
        result = await self.send()
        self.assertEqual(result["status"], "completed")
        self.client.make_inter_wallet_transfer.assert_awaited_once()

    async def test_acknowledgement_without_settlement_does_not_post_balances(self):
        self.client.make_inter_wallet_transfer.return_value = {"result":True,"data":[{"TX_ID":"TRANSFER1"}]}
        with self.assertRaises(ReconciliationRequired): await self.send()
        await self.db.refresh(self.sender); await self.db.refresh(self.receiver)
        self.assertEqual(self.sender.cached_balance_minor,10000)
        self.assertEqual(self.receiver.cached_balance_minor,0)
        with self.assertRaises(ReconciliationRequired): await self.send()
        self.client.make_inter_wallet_transfer.assert_awaited_once()


class WalletRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db,self.engine=await local_session()
        self.keys=SimpleNamespace(create_keystore=AsyncMock(return_value="0xnew"),verify_key=AsyncMock(return_value=True),
                                  update_key_password=AsyncMock(return_value={"result":True}))
        self.tns=SimpleNamespace(assert_name_available=AsyncMock(),set_name=AsyncMock(),
                                 get_address_for_name=AsyncMock(return_value="0xnew"))
        from app.services.billing.wallet_service import ToroForgeWalletService
        self.service=ToroForgeWalletService(self.db,self.keys,self.tns)
        self.cache=patch("app.services.billing.wallet_service.invalidate_billing_scope");self.cache.start()
        self.clinic_id=uuid4()
    async def asyncTearDown(self):self.cache.stop();await close_local_session(self.db,self.engine)
    async def provision(self):
        return await self.service.create_and_provision_wallet(username="testname",wallet_type=WalletType.CLINIC,
                                                              clinic_id=self.clinic_id,dso_id=None)
    async def test_naming_timeout_recovers_by_read_without_creating_another_wallet(self):
        self.tns.set_name.side_effect=TimeoutError("unknown")
        with self.assertRaises(TimeoutError):await self.provision()
        wallet=(await self.db.scalars(select(Wallet))).one()
        self.assertEqual(wallet.external_wallet_address,"0xnew")
        self.assertTrue(wallet.external_wallet_password_encrypted)
        result=await self.provision()
        self.assertEqual(result.wallet_id,wallet.id)
        self.assertEqual(wallet.status,WalletStatus.ACTIVE)
        self.keys.create_keystore.assert_awaited_once()
        self.tns.set_name.assert_awaited_once()
        self.tns.get_address_for_name.assert_awaited_once()
    async def test_keystore_timeout_never_mints_a_second_key(self):
        self.keys.create_keystore.side_effect=TimeoutError("unknown")
        with self.assertRaises(TimeoutError):await self.provision()
        with self.assertRaises(ReconciliationRequired):await self.provision()
        self.keys.create_keystore.assert_awaited_once()
    async def test_rotation_recovers_only_after_new_password_is_verified(self):
        from app.security.encryption import decode_secret
        wallet=Wallet(id=uuid4(),clinic_id=self.clinic_id,wallet_type=WalletType.CLINIC,
                      external_wallet_address="0xnew",external_wallet_password_encrypted=encrypt_secret("old"))
        self.db.add(wallet);await self.db.commit()
        self.keys.update_key_password.side_effect=TimeoutError("unknown")
        with self.assertRaises(TimeoutError):await self.service.update_wallet_password(wallet_id=wallet.id)
        op=(await self.db.scalars(select(ExternalOperation))).one()
        candidate=decode_json_secret(op.secrets_encrypted)["new_password"]
        await self.db.refresh(wallet)
        self.assertEqual(decode_secret(wallet.external_wallet_password_encrypted),"old")
        self.keys.verify_key.return_value=False
        with self.assertRaises(ReconciliationRequired):await self.service.update_wallet_password(wallet_id=wallet.id)
        self.keys.verify_key.return_value=True
        await self.service.update_wallet_password(wallet_id=wallet.id)
        self.assertEqual(decode_secret(wallet.external_wallet_password_encrypted),candidate)
        self.assertEqual(op.state,"applied")
        self.keys.update_key_password.assert_awaited_once()


class FundingPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db,self.engine=await local_session()
        self.wallet=Wallet(id=uuid4(),clinic_id=uuid4(),wallet_type=WalletType.CLINIC,status=WalletStatus.ACTIVE,
            external_wallet_address="0xabc",kyc_verified=True,currency="USD",cached_balance_minor=0)
        self.db.add(self.wallet);await self.db.commit()
        self.client=SimpleNamespace(initialize_payment=AsyncMock(return_value={"result":True,"data":[{"TX_ID":"TX1"}]}))
        self.client.client=SimpleNamespace(config=SimpleNamespace(admin="test",adminpwd="test"))
        self.service=ToroForgeFundingService(self.db,self.client)
        self.cache=patch("app.services.billing.funding_service.invalidate_billing_scope");self.cache.start()
    async def asyncTearDown(self):self.cache.stop();await close_local_session(self.db,self.engine)
    async def initialize(self,key="one",amount="10",wallet_id=None):
        return await self.service.initialize_wallet_funding(wallet_id=wallet_id or self.wallet.id,request_id=key,amount=amount,
            currency="USD",payment_type="card",success_url="https://example.com/ok",cancel_url="https://example.com/no")
    async def test_key_reuse_is_scoped_and_request_bound(self):
        first=await self.initialize()
        self.assertEqual(first["payment_transaction_id"],(await self.initialize())["payment_transaction_id"])
        with self.assertRaises(ToroForgeValidationError):await self.initialize(amount="20")
        second=Wallet(id=uuid4(),clinic_id=uuid4(),wallet_type=WalletType.CLINIC,status=WalletStatus.ACTIVE,
            external_wallet_address="0xsecond",kyc_verified=True,currency="USD")
        self.db.add(second);await self.db.commit()
        self.client.initialize_payment.return_value={"result":True,"data":[{"TX_ID":"TX2"}]}
        self.assertNotEqual(first["payment_transaction_id"],(await self.initialize(wallet_id=second.id))["payment_transaction_id"])
        self.assertEqual(self.client.initialize_payment.await_count,2)
    async def test_timeout_cannot_be_repeated_with_different_key(self):
        self.client.initialize_payment.side_effect=TimeoutError("unknown")
        with self.assertRaises(TimeoutError):await self.initialize()
        self.assertEqual((await self.initialize())["status"],"pending")
        with self.assertRaises(ReconciliationRequired):await self.initialize(key="another")
        self.client.initialize_payment.assert_awaited_once()
    async def test_successful_lookup_credits_once_without_settlement_configuration(self):
        from app.models.entities import PaymentTransaction
        await self.initialize()
        payment=(await self.db.scalars(select(PaymentTransaction))).one()
        evidence={"result":True,"data":[{"TX_ID":"TX1","TX_Currency":"USD","TX_Address":"0xabc","TX_Amount":"10"}]}
        self.client.get_payment_by_txid=AsyncMock(return_value=evidence)
        for _ in range(2):
            result=await self.service.verify_wallet_deposit(wallet_id=self.wallet.id,
                payment_transaction_id=payment.id,txid="TX1")
            self.assertEqual(result["status"],"succeeded")
        self.assertEqual(self.wallet.cached_balance_minor,1000)
        self.assertNotIn("provider_response",payment.details)
        self.assertEqual(decode_json_secret(payment.details["provider_response_encrypted"])["result"],True)

    async def test_unsuccessful_lookup_never_credits(self):
        from app.models.entities import PaymentTransaction
        await self.initialize()
        payment=(await self.db.scalars(select(PaymentTransaction))).one()
        # Capture ids once: each rejection rolls back, which expires the loaded rows.
        payment_id,wallet_id=payment.id,self.wallet.id
        record={"TX_ID":"TX1","TX_Currency":"USD","TX_Address":"0xabc","TX_Amount":"10"}
        for flag in (False,None,"true"):
            self.client.get_payment_by_txid=AsyncMock(return_value={"result":flag,"data":[record]})
            with self.assertRaises(ToroForgeValidationError):
                await self.service.verify_wallet_deposit(wallet_id=wallet_id,payment_transaction_id=payment_id,txid="TX1")
            with self.assertRaises(ToroForgeValidationError):
                await self.service.record_verified_funding_success(payment_transaction_id=payment_id,
                    provider_response={"result":flag,"matched_record":record})
            await self.db.refresh(self.wallet)
            self.assertEqual(self.wallet.cached_balance_minor,0)


class DatabaseInvariantTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.db,self.engine=await local_session()
    async def asyncTearDown(self):await close_local_session(self.db,self.engine)
    async def test_multiple_clinic_wallets_allowed_but_second_treasury_rejected(self):
        from sqlalchemy.exc import IntegrityError
        dso=uuid4()
        self.db.add_all([Wallet(id=uuid4(),dso_id=dso,clinic_id=uuid4(),wallet_type=WalletType.CLINIC),
                         Wallet(id=uuid4(),dso_id=dso,clinic_id=uuid4(),wallet_type=WalletType.CLINIC),
                         Wallet(id=uuid4(),dso_id=dso,wallet_type=WalletType.DSO_TREASURY)])
        await self.db.commit()
        self.db.add(Wallet(id=uuid4(),dso_id=dso,wallet_type=WalletType.DSO_TREASURY))
        with self.assertRaises(IntegrityError):await self.db.commit()
        await self.db.rollback()
        self.assertEqual(await self.db.scalar(select(func.count()).select_from(Wallet)),3)
    async def test_duplicate_active_membership_is_rejected_but_inactive_history_allowed(self):
        from sqlalchemy.exc import IntegrityError
        from app.models.entities import RoleAssignment,RoleType,ScopeType
        user,dso=uuid4(),uuid4()
        self.db.add_all([RoleAssignment(user_id=user,dso_id=dso,scope_type=ScopeType.DSO,role=RoleType.ADMIN,is_active=True),
                         RoleAssignment(user_id=user,dso_id=dso,scope_type=ScopeType.DSO,role=RoleType.ADMIN,is_active=False)])
        await self.db.commit()
        self.db.add(RoleAssignment(user_id=user,dso_id=dso,scope_type=ScopeType.DSO,role=RoleType.ADMIN,is_active=True))
        with self.assertRaises(IntegrityError):await self.db.commit()
        await self.db.rollback()
    async def test_deleting_appointment_cannot_cascade_up_to_patient_or_clinic(self):
        from app.models.entities import Appointments
        from sqlalchemy import inspect
        mapper=inspect(Appointments)
        for name in ("clinic","patient"):
            self.assertNotIn("delete",mapper.relationships[name].cascade)
