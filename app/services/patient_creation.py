from app.infrastructure.providers.opendental import openDentalApi, OpenDentalRequestError
from app.services.external_operations import ExternalOperations
from datetime import date
from app.models.entities import Patients
from app.schemas.api import patient_model
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from app.security.encryption import encrypt_secret, fingerprint_value, hash_lookup
import logging
from uuid import UUID

log = logging.getLogger(__name__)

class PatientService():
    def __init__(self, db, od_client, clinic_id: UUID ):
        self.operations = ExternalOperations(db)
        self.od = od_client
        self.db = db 
        self.clinic_id = clinic_id

    async def reserve_patient(self, contact_id : str,   pat : patient_model ):
        contact_hash = hash_lookup(contact_id)
        row = self.db.query(Patients).filter_by(contact_id_hash=contact_hash, clinic_id=self.clinic_id).first()
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
            self.db.commit()
            self.db.refresh(row)
            return row 
        
        except IntegrityError:
            self.db.rollback()
            row = (
                self.db.query(Patients)
                .filter_by(clinic_id=self.clinic_id, contact_id_hash=contact_hash)
                .first()
            ) 
            return row
        
    async  def finalize_into_db(self, row: Patients, pat_num):
        try:
            row.pat_num = pat_num
            self.db.commit ()
            return(row.id, pat_num)
        except IntegrityError:
            self.db.rollback()
            existing = (
                self.db.query(Patients)
                .filter_by(clinic_id=self.clinic_id, pat_num=pat_num)
                .first()
            )
            if existing:
                raise OpenDentalRequestError("Patient is already linked to another contact; manual resolution is required")
            raise

        except SQLAlchemyError:
            self.db.rollback()
            raise


    async def resolve_patnum (self, pat: patient_model, contact_id : str ):
        if not contact_id:
            return None
        
        if not pat.FName.strip() or not pat.LName.strip() or not pat.Birthdate:
            raise OpenDentalRequestError("Patient name and date of birth are required for reliable matching")
        try:
            date.fromisoformat(pat.Birthdate)
        except ValueError as exc:
            raise OpenDentalRequestError("Patient date of birth is invalid") from exc

        row =  await self.reserve_patient(
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
            raise OpenDentalRequestError("Patient identity is ambiguous; manual resolution is required")
        if candidates:
            return await self.finalize_into_db(row, candidates[0]["PatNum"])

        key = f"patient-create:{row.id}"
        async def create():
            created = await self.od.create_patient(patient_data=pat)
            if not isinstance(created, dict) or not created.get("PatNum"):
                raise ValueError("Patient creation result requires reconciliation")
            return {"PatNum": int(created["PatNum"])}
        created = await self.operations.execute(key=key, resource_id=row.id, action="patient_create",
            request=pat.model_dump(mode="json"), call=create)
        self.operations.mark_applied(key)
        return await self.finalize_into_db(row, created["PatNum"])
