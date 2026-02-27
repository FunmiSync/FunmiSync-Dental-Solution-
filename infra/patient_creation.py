from sdk.opendental_sdk import openDentalApi
from core.models import Patients
from core.schemas import patient_model
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import logging

log = logging.getLogger(__name__)

class PatientService():
    def __init__(self, db, od_client, clinic_id: str ):
        self.od = od_client
        self.db = db 
        self.clinic_id = clinic_id


    async def reserve_patient(self, contact_id : str,   pat : patient_model ):
        row = self.db.query(Patients).filter_by(contact_id = contact_id , clinic_id = self.clinic_id).first()
        if row:
            return row 
        
        row = Patients(
            contact_id = contact_id,
            FName = pat.FName,
            LName = pat.LName,
            Gender = pat.Gender,
            phone = pat.WirelessPhone,         
            Email = pat.Email, 
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
                .filter_by(clinic_id=self.clinic_id, contact_id=contact_id)
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
                return (existing.id, int(existing.pat_num))
            raise

        except SQLAlchemyError:
            self.db.rollback()
            raise


    async def resolve_patnum (self, pat: patient_model, contact_id : str ):
        if not contact_id:
            return None
        
        row =  await self.reserve_patient(
            contact_id = contact_id,
            pat = pat 
        )
        if row.pat_num:
            return(row.id, row.pat_num)
        
        
        try:
           matches =  await self.od.search_patients(last_name = pat.LName, date_of_birth = pat.Birthdate)
        except Exception as e :
            log.error("OD failed to search for patient", extra = {
                "clinic" : self.clinic_id ,
                "contact_id" : contact_id

            })

            raise 

        if matches:
            pat_num = matches[0]["PatNum"]
            return  await self.finalize_into_db(row, pat_num)
                  
        try:
            log.info(f"creating patient on od for contact_id{contact_id} in clinic {self.clinic_id}") 
            created = await self.od.create_patient(patient_data = pat)
        except Exception as e : 
            log.error(f"Failed to create patient in OD for {contact_id} in clinic {self.clinic_id}")
            raise

        if created:
            pat_num = created["PatNum"] 
            return await self.finalize_into_db(row, pat_num)
            
        
        