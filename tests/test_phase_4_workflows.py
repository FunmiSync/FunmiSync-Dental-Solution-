from contextlib import ExitStack
from datetime import datetime, timezone
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from runtime_support import configure_test_environment, guarded_local_services
configure_test_environment()

from sqlalchemy import func, select
from local_database import close_local_session, local_session, local_sync_session
from app.models.entities import RegisteredClinics, Patients, Dso, RoleAssignment, Audit_logs, ExternalOperation
from app.schemas.api import patient_model, AppointmentRequest, usercreate, cliniccreate, Webhook_requests
from app.services.patient_creation import PatientService
from app.services.appointment_service import AppointmentService
from app.services.external_operations import ReconciliationRequired
from app.security.encryption import encrypt_secret, decode_secret, decode_json_secret
from app.infrastructure.providers.opendental import OpenDentalRequestError

_guard = None
def setUpModule():
    global _guard
    _guard = guarded_local_services();_guard.__enter__()
def tearDownModule():
    _guard.__exit__(None,None,None)


def clinic_row():
    return RegisteredClinics(id=uuid4(),crm_type="ghl",clinic_name="Test",clinic_number=1,clinic_timezone="UTC",
        od_developer_key=encrypt_secret("key"),od_customer_key=encrypt_secret("key"),crm_api_key=encrypt_secret("key"),
        location_id="location",calendar_id=encrypt_secret("calendar"),owner_id=uuid4())


class PatientMatchingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db,self.engine=await local_session();self.clinic=clinic_row();self.db.add(self.clinic);await self.db.commit()
        self.patient=patient_model(FName="Test",LName="Patient",Birthdate="2000-01-01",Gender="Unknown")
        self.provider=SimpleNamespace(search_patients=AsyncMock(return_value=[]),create_patient=AsyncMock(return_value={"PatNum":12}))
        self.service=PatientService(self.db,self.provider,self.clinic.id)
    async def asyncTearDown(self):await close_local_session(self.db,self.engine)

    async def test_missing_birthdate_does_not_search_or_create(self):
        self.patient.Birthdate=None
        with self.assertRaises(OpenDentalRequestError):await self.service.resolve_patnum(self.patient,"contact")
        self.provider.search_patients.assert_not_awaited();self.provider.create_patient.assert_not_awaited()

    async def test_multiple_identity_matches_require_manual_resolution(self):
        record={"PatNum":12,"FName":"Test","LName":"Patient","Birthdate":"2000-01-01"}
        self.provider.search_patients.return_value=[record,record|{"PatNum":13}]
        with self.assertRaises(OpenDentalRequestError):await self.service.resolve_patnum(self.patient,"contact")
        self.provider.create_patient.assert_not_awaited()

    async def test_a_partial_name_match_is_not_silently_selected(self):
        self.provider.search_patients.return_value=[{"PatNum":12,"LName":"Patient"}]
        with self.assertRaises(OpenDentalRequestError):await self.service.resolve_patnum(self.patient,"contact")
        self.provider.create_patient.assert_not_awaited()

    async def test_created_patient_is_reused_and_demographics_are_encrypted(self):
        first=await self.service.resolve_patnum(self.patient,"contact")
        second=await self.service.resolve_patnum(self.patient,"contact")
        self.assertEqual(first,second);self.provider.create_patient.assert_awaited_once()
        row=(await self.db.scalars(select(Patients))).one()
        self.assertNotIn("Patient",row.LName);self.assertEqual(decode_secret(row.Gender),"Unknown")

    async def test_unknown_creation_never_blindly_creates_again(self):
        self.provider.create_patient.side_effect=TimeoutError("unknown")
        with self.assertRaises(TimeoutError):await self.service.resolve_patnum(self.patient,"contact")
        with self.assertRaises(ReconciliationRequired):await self.service.resolve_patnum(self.patient,"contact")
        self.provider.create_patient.assert_awaited_once()


class AppointmentJournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db,self.engine=await local_session();self.clinic=clinic_row();self.db.add(self.clinic);await self.db.commit()
        self.provider=SimpleNamespace(create_appointments=AsyncMock(return_value={"AptNum":42}),
            create_commslog=AsyncMock(return_value={}),create_pops=AsyncMock(return_value={}))
        self.service=AppointmentService(self.db,self.clinic,self.provider)
        self.req=AppointmentRequest(date_str="2026-10-17",start_str="09:00",end_str="09:30",status="Scheduled",
            calendar_id="calendar",event_id="event",contact_id="contact",pat_id=uuid4(),pat_Num=12,clinic_timezone="UTC",
            commslog="called",pop_up="note")
    async def asyncTearDown(self):await close_local_session(self.db,self.engine)

    async def create(self):
        return await self.service.create_appointment(self.req,"XXXXXX",1,datetime(2026,10,17,9))

    async def test_creation_result_can_resume_without_duplicate_appointment(self):
        self.assertEqual(await self.create(),{"AptNum":42})
        self.assertEqual(await self.create(),{"AptNum":42})
        self.provider.create_appointments.assert_awaited_once()

    async def test_popup_and_commlog_receipts_survive_local_flag_failure(self):
        await self.service.handle_commslog(self.req);await self.service.handle_popups(self.req)
        await self.service.handle_commslog(self.req);await self.service.handle_popups(self.req)
        self.provider.create_commslog.assert_awaited_once();self.provider.create_pops.assert_awaited_once()

    async def test_booking_availability_can_bypass_a_stale_cache(self):
        self.provider.get_appointments_in_operatory=AsyncMock(return_value=[])
        with patch("app.services.appointment_service.get_operatory_day_appointments_cached") as cache, \
             patch("app.services.appointment_service.set_operatory_day_appointments_cached"):
            await self.service.get_operatory_appointments_cached(operatory=1,date_start="2026-10-17",date_end="2026-10-17",fresh=True)
        cache.assert_not_called();self.provider.get_appointments_in_operatory.assert_awaited_once()


class DataPrivacyTests(unittest.TestCase):
    def test_blind_name_index_supports_prefix_without_plaintext(self):
        from app.security.search_index import name_index,name_search_token
        index=name_index("Test Patient")
        self.assertNotIn("patient",index.lower());self.assertIn(name_search_token("pati"),index)
        self.assertNotIn(name_search_token("unrelated"),index)

    def test_cache_ciphertext_is_not_plain_json(self):
        from app.infrastructure.cache.resilience import encode_cache_payload,decode_cache_payload
        value={"patient_name":"Test Patient"};stored=encode_cache_payload(value)
        self.assertNotIn("Test Patient",stored);self.assertEqual(decode_cache_payload(stored),value)

    def test_change_key_includes_event_and_payload_changes(self):
        from app.services.appointment_sync_log_helper import AppointmentSyncLogService,SyncLogInput
        data=SyncLogInput(clinic_id=uuid4(),inbound_event_id=None,pat_id=None,appointment_id=None,contact_id="contact",
            event_id="one",apt_num=None,patient_name=None,date_str="2026-10-17",start_str="09:00",end_str="09:30",
            appointment_status="Scheduled",direction=__import__('app.models.entities',fromlist=['SyncDirection']).SyncDirection.CRM_TO_OD,payload={"note":"first"})
        service=AppointmentSyncLogService(MagicMock());first=service.build_change_key(data)
        data.event_id="two";self.assertNotEqual(first,service.build_change_key(data))
        data.event_id="one";data.payload={"note":"updated"};self.assertNotEqual(first,service.build_change_key(data))

    def test_backfill_dry_run_is_non_mutating_then_idempotent(self):
        from tools.backfill_private_data import plan
        db,engine=local_sync_session()
        try:
            c=clinic_row();db.add(c);db.commit()
            row=Patients(id=uuid4(),FName="Test",LName="Patient",Gender="Unknown",phone="123",email="x@example.com",
                contact_id="contact",contact_id_hash=None,clinic_id=c.id,pat_num=12)
            db.add(row);db.commit();changes,counts=plan(db)
            self.assertEqual(row.FName,"Test");self.assertFalse(db.dirty);self.assertEqual(counts["patients"],1)
            for entity,updates in changes:
                for name,value in updates.items():setattr(entity,name,value)
            db.commit();self.assertEqual(plan(db)[0],[])
            self.assertEqual(decode_secret(row.FName),"Test")
        finally:db.close();engine.dispose()


class RegistrationAndValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dso_and_admin_role_commit_together_and_rollback_together(self):
        from app.services.registration import create_dso
        db,engine=await local_session()
        count=lambda model: db.scalar(select(func.count()).select_from(model))
        try:
            user=uuid4()
            with patch.object(db,"commit",side_effect=RuntimeError("failure")), self.assertRaises(RuntimeError):
                await create_dso(db,user_id=user,name="Test")
            self.assertEqual(await count(Dso),0);self.assertEqual(await count(RoleAssignment),0)
            await create_dso(db,user_id=user,name="Test")
            self.assertEqual(await count(Dso),1);self.assertEqual(await count(RoleAssignment),1)
            audit=(await db.scalars(select(Audit_logs))).one();self.assertEqual(audit.action,"create")
            self.assertEqual(decode_json_secret(audit.details["encrypted"])["actor_user_id"],str(user))
        finally:await close_local_session(db,engine)

    def test_password_bcrypt_limit_uses_bytes_and_names_cannot_be_blank(self):
        for data in ({"username":" ","password":"password"},{"username":"name","password":" "},
                     {"username":"name","password":"é"*37}):
            with self.assertRaises(ValidationError):usercreate(email="x@example.com",**data)


def lock_session():
    # An AsyncSession whose bind hands out one mocked async connection.
    connection=MagicMock(name="AsyncConnection")
    for name in ("execute","commit","invalidate","close"):
        setattr(connection,name,AsyncMock(name=name))
    db=MagicMock(name="AsyncSession");db.bind.dialect.name="postgresql"
    db.bind.connect=AsyncMock(return_value=connection)
    return db,connection


class ClinicLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_lock_never_enters_workflow_and_closes_connection(self):
        from app.infrastructure.clinic_lock import clinic_sync_lock,ClinicBusyError
        db,connection=lock_session()
        connection.execute.return_value=SimpleNamespace(scalar_one=lambda:False)
        with self.assertRaises(ClinicBusyError):
            async with clinic_sync_lock(db,uuid4()):self.fail("busy workflow entered")
        connection.close.assert_awaited_once()

    async def test_unlock_failure_invalidates_connection_before_pool_return(self):
        from app.infrastructure.clinic_lock import clinic_sync_lock
        db,connection=lock_session()
        connection.execute.side_effect=[SimpleNamespace(scalar_one=lambda:True),RuntimeError("unlock failed")]
        with self.assertRaises(RuntimeError):
            async with clinic_sync_lock(db,uuid4()):pass
        connection.invalidate.assert_awaited_once();connection.close.assert_awaited_once()


class ReadinessAndStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_reports_dependency_outage_as_503(self):
        from app.controllers.health import readiness
        with patch("app.controllers.health.ping_db",return_value=(False,"failure")), \
             patch("app.controllers.health.async_redis",SimpleNamespace(ping=AsyncMock(return_value=True))):
            response=await readiness()
        self.assertEqual(response.status_code,503);self.assertFalse(json.loads(response.body)["database"])

    async def test_revoked_stream_sends_no_waiting_event_and_closes_pubsub(self):
        # Access is granted when the stream opens, then revoked while it waits for an event.
        from app.controllers.sync_logs import sync_log_clinic as module
        from app.controllers.sync_logs import streaming
        from app.security import stream_access
        request=SimpleNamespace(headers={"Authorization":"Bearer token"},cookies={},is_disconnected=AsyncMock(return_value=False))
        pubsub=SimpleNamespace(subscribe=AsyncMock(),get_message=AsyncMock(return_value={"type":"message","data":"private event"}),
            unsubscribe=AsyncMock(),aclose=AsyncMock())
        # Each permission check opens `async with AsyncSessionLocal() as db`.
        session=MagicMock(name="session context")
        session.__aenter__=AsyncMock(return_value=MagicMock(name="AsyncSession"))
        session.__aexit__=AsyncMock(return_value=False)
        with patch.object(stream_access,"AsyncSessionLocal",return_value=session), \
             patch.object(stream_access,"get_current_user",return_value=SimpleNamespace(id=uuid4())), \
             patch.object(stream_access,"require_clinic_access",side_effect=[None,HTTPException(403)]) as access, \
             patch.object(streaming,"async_redis",SimpleNamespace(pubsub=lambda:pubsub)):
            response=await module.stream_clinic_sync_logs_events(uuid4(),request)
            body="".join([part async for part in response.body_iterator])
        self.assertIn("access_revoked",body);self.assertNotIn("private event",body)
        self.assertEqual(access.call_count,2)
        # Every permission check opens its own session and must close it.
        pubsub.aclose.assert_awaited_once();self.assertEqual(session.__aexit__.await_count,2)

    async def test_stream_without_token_is_rejected_before_subscribing(self):
        from app.controllers.sync_logs import sync_log_dso as module
        from app.controllers.sync_logs import streaming
        request=SimpleNamespace(headers={},cookies={},url=SimpleNamespace(path="/dsos/x/sync-logs/stream"))
        pubsub=MagicMock()
        with patch.object(streaming,"async_redis",SimpleNamespace(pubsub=lambda:pubsub)), \
             self.assertRaises(HTTPException) as caught:
            await module.stream_dso_sync_logs_page(uuid4(),request)
        self.assertEqual(caught.exception.status_code,401)
        pubsub.subscribe.assert_not_called()


class ProviderBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ghl_decodes_json_and_never_retries_unknown_writes(self):
        import httpx
        from app.infrastructure.providers.ghl import GHL
        service=GHL("test","location","test")
        service.cb=MagicMock()
        response=httpx.Response(200,json={"contact":{"id":"123"}},request=httpx.Request("GET","https://example.com"))
        with patch("app.infrastructure.providers.ghl.httpx.AsyncClient") as client, \
             patch("app.infrastructure.providers.ghl.retry_with_bak_off",new_callable=AsyncMock) as retry:
            request=client.return_value.__aenter__.return_value.request
            request.return_value=response
            result=await service._request("POST","/contacts",json={"firstName":"Test"})
            self.assertEqual(result,{"contact":{"id":"123"}})
            retry.assert_not_awaited()
            request.side_effect=httpx.ReadTimeout("unknown")
            with self.assertRaises(httpx.ReadTimeout):await service._request("POST","/contacts",json={})
            self.assertEqual(request.await_count,2)
            retry.assert_not_awaited()
    def test_login_key_does_not_contain_email_or_address(self):
        from app.services.auth.login_attempts import login_attempts
        key=login_attempts("Person@example.com","127.0.0.1")
        self.assertNotIn("Person",key);self.assertNotIn("example.com",key);self.assertNotIn("127.0.0.1",key)
        self.assertEqual(key,login_attempts(" person@example.com ","127.0.0.1"))


class MigrationPreparationTests(unittest.TestCase):
    def migration(self):
        import importlib.util
        from pathlib import Path
        path=Path(__file__).resolve().parents[1]/"alembic/versions/f4a100000001_external_operations_and_wallet_scope.py"
        spec=importlib.util.spec_from_file_location("phase4_revision",path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    def test_revision_is_single_head_and_existing_chain_is_preserved(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        script=ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(script.get_heads(),["f4a100000001"])
        self.assertEqual(script.get_revision("f4a100000001").down_revision,"d64762c7874a")
    def test_migration_declares_journal_columns_and_partial_indexes(self):
        import sqlalchemy as sa
        from app.models.entities import ExternalOperation
        module=self.migration();fake=MagicMock()
        fake.get_bind.return_value.execute.return_value.scalar_one.return_value=0
        with patch.object(module,"op",fake):module.upgrade()
        args=fake.create_table.call_args.args
        table=sa.Table(args[0],sa.MetaData(),*args[1:])
        expected=ExternalOperation.__table__
        self.assertEqual(set(table.columns.keys()),set(expected.columns.keys()))
        for column in table.columns:
            self.assertEqual(column.nullable,expected.c[column.name].nullable,column.name)
            self.assertEqual(type(column.type),type(expected.c[column.name].type),column.name)
        names={call.args[0] for call in fake.create_index.call_args_list}
        self.assertTrue({"uq_external_operation_active_resource","uq_wallet_dso_treasury",
                         "uq_active_dso_membership","uq_active_clinic_membership"}.issubset(names))
        fake.drop_constraint.assert_called_once_with("up_wallet_dso_type","wallets",type_="unique")
    def test_invalid_ownership_aborts_before_schema_changes(self):
        module=self.migration();fake=MagicMock()
        fake.get_bind.return_value.execute.return_value.scalar_one.return_value=1
        with patch.object(module,"op",fake),self.assertRaises(RuntimeError):module.upgrade()
        fake.drop_constraint.assert_not_called();fake.create_table.assert_not_called()
    def test_duplicate_membership_aborts_and_journal_downgrade_is_blocked(self):
        module=self.migration();fake=MagicMock()
        fake.get_bind.return_value.execute.return_value.scalar_one.side_effect=[0,1]
        with patch.object(module,"op",fake),self.assertRaises(RuntimeError):module.upgrade()
        fake.execute.assert_not_called()
        with self.assertRaises(RuntimeError):module.downgrade()
