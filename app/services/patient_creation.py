from app.infrastructure.providers.opendental import openDentalApi, OpenDentalRequestError
from app.services.external_operations import ExternalOperations, OutcomeNotKnownYet
from app.services.reconciliation_checks import OPENDENTAL_NOT_DONE_AFTER, WAITING, as_utc, not_done_or_wait
from datetime import date, datetime, timezone
from sqlalchemy import select
from app.models.entities import Patients
from app.schemas.api import patient_model
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.security.encryption import encrypt_secret, fingerprint_value, hash_lookup
import logging
from uuid import UUID

log = logging.getLogger(__name__)


class PatientMatchError(OpenDentalRequestError):
    """The CRM contact cannot be matched to one OpenDental patient; the clinic must sort it out."""

class PatientService():
    def __init__(self, db, od_client, clinic_id: UUID ):
        self.operations = ExternalOperations(db)
        self.od = od_client
        self.db = db 
        self.clinic_id = clinic_id

    async def reserve_patient(self, contact_id : str,   pat : patient_model ):
        contact_hash = hash_lookup(contact_id)
        row = await self.db.scalar(select(Patients).filter_by(contact_id_hash=contact_hash, clinic_id=self.clinic_id).limit(1))
        if row:
            return row 
        
        row = Patients(
            contact_id = encrypt_secret(contact_id),
            contact_id_hash=contact_hash,
            FName = encrypt_secret(pat.FName) if pat.FName else None,
            LName = encrypt_secret(pat.LName) if pat.LName else None,
            Gender = encrypt_secret(pat.Gender or "Unknown"),
            phone = encrypt_secret(pat.WirelessPhone or ""),
            email = encrypt_secret(str(pat.Email)) if pat.Email else None,
            clinic_id = self.clinic_id,
            pat_num = None
        )

        try:
            self.db.add(row)
            await self.db.commit()
            await self.db.refresh(row)
            return row 
        
        except IntegrityError:
            await self.db.rollback()
            row = (
                await self.db.scalar(select(Patients)
                .filter_by(clinic_id=self.clinic_id, contact_id_hash=contact_hash).limit(1))
            ) 
            return row
        
    async def finalize_into_db(self, row: Patients, pat_num):
        try:
            row.pat_num = pat_num
            await self.db.commit()
            return(row.id, pat_num)
        except IntegrityError:
            await self.db.rollback()
            existing = (
                await self.db.scalar(select(Patients)
                .filter_by(clinic_id=self.clinic_id, pat_num=pat_num).limit(1))
            )
            if existing:
                raise PatientMatchError("Patient is already linked to another contact; manual resolution is required")
            raise

        except SQLAlchemyError:
            await self.db.rollback()
            raise


    async def _settle_earlier_create(self, key: str, *, found_pat_num) -> None:
        """An earlier attempt to add this patient timed out. The search just made answers it:
        found means it was created; nothing found for OPENDENTAL_NOT_DONE_AFTER means it was not.
        Raises OutcomeNotKnownYet while it is too early to tell."""
        operation = await self.operations.get(key)
        if not self.operations.unknown(operation):
            return
        if found_pat_num is not None:
            await self.operations.settle_from_provider_read(
                operation, result={"PatNum": int(found_pat_num)}, evidence=f"OpenDental has the patient: PatNum {found_pat_num}")
            await self.operations.mark_applied(key)  # committed with the patient row by finalize_into_db
            return
        finding = not_done_or_wait(datetime.now(timezone.utc) - as_utc(operation.created_at),
                                   "OpenDental has no such patient", after=OPENDENTAL_NOT_DONE_AFTER)
        if finding.outcome == WAITING:
            raise OutcomeNotKnownYet(f"An earlier attempt to add this patient timed out. {finding.evidence}")
        await self.operations.settle_from_provider_read(operation, result=None, evidence=finding.evidence)

    async def resolve_patnum (self, pat: patient_model, contact_id : str ):
        if not contact_id:
            return None
        
        if not pat.FName.strip() or not pat.LName.strip() or not pat.Birthdate:
            raise PatientMatchError("Patient name and date of birth are required for reliable matching")
        try:
            date.fromisoformat(pat.Birthdate)
        except ValueError as exc:
            raise PatientMatchError("Patient date of birth is invalid") from exc

        row = await self.reserve_patient(
            contact_id = contact_id,
            pat = pat 
        )
        if row.pat_num is not None:
            return(row.id, row.pat_num)
        
        
        try:
           matches =  await self.od.search_patients(last_name = pat.LName, date_of_birth = pat.Birthdate)
        except Exception as e :
            log.error("OD failed to search for patient", extra = {
                "clinic" : self.clinic_id ,
                "contact_ref" : fingerprint_value(contact_id)

            })

            raise 

        def normalized(value):
            return " ".join(str(value or "").casefold().split())
        candidates = [item for item in (matches or []) if
            normalized(item.get("FName")) == normalized(pat.FName) and
            normalized(item.get("LName")) == normalized(pat.LName) and
            str(item.get("Birthdate") or "")[:10] == pat.Birthdate]
        if len(candidates) > 1 or (matches and not candidates):
            raise PatientMatchError("Patient identity is ambiguous; manual resolution is required")
        key = f"patient-create:{row.id}"
        if candidates:
            await self._settle_earlier_create(key, found_pat_num=candidates[0]["PatNum"])
            return await self.finalize_into_db(row, candidates[0]["PatNum"])

        await self._settle_earlier_create(key, found_pat_num=None)
        async def create():
            created = await self.od.create_patient(patient_data=pat)
            if not isinstance(created, dict) or not created.get("PatNum"):
                raise ValueError("Patient creation result requires reconciliation")
            return {"PatNum": int(created["PatNum"])}
        created = await self.operations.execute(key=key, resource_id=row.id, action="patient_create",
            request=pat.model_dump(mode="json"), call=create)
        await self.operations.mark_applied(key)
        return await self.finalize_into_db(row, created["PatNum"])
