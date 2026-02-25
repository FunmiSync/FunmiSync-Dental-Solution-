from core.database import SessionLocal
from core.models import Patients, RegisteredClinics, Appointments
from sdk.opendental_sdk import openDentalApi
from fastapi import HTTPException
from core.schemas import patient_model
from infra.appointment_service import AppointmentService
from infra.patient_creation import PatientService
from core.schemas import AppointmentRequest
from sqlalchemy.exc import SQLAlchemyError
from core.circuti_breaker import circuit_breaker_open_error
import logging

logger = logging.getLogger(__name__)


async def process_crm_load(clinic_id: str, crm_type: str, payload: dict):
    db = SessionLocal()
    try:
        logger.info("CRM job started", extra={
            "clinic_id": clinic_id,
            "crm_type": crm_type,
            "event_id": payload.get("event_id"),
            "contact_id": payload.get("contact_id"),
        })

        clinic = db.query(RegisteredClinics).filter_by(id=clinic_id).first()
        if not clinic:
            raise ValueError("clinic id  {clinic} not found ")

        timezone = clinic.clinic_timezone
        patient_data = patient_model(
            FName=payload.get("first_name", ""),
            LName=payload.get("last_name", ""),
            Gender=payload.get("Gender"),
            Address=payload.get("Address"),
            Birthdate=payload.get("BirthDate"),
            WirelessPhone=payload.get("WirelessPhone"),
            Email=payload.get("Email"),
        )
        logger.info("Patient payload mapped", extra={
            "clinic_id": clinic_id,
            "event_id": payload.get("event_id"),
            "contact_id": payload.get("contact_id"),
            "has_fname": bool(patient_data.FName),
            "has_lname": bool(patient_data.LName),
            "has_birthdate": bool(patient_data.Birthdate),
        })

        commslog = payload.get("commslog", "")
        date_str = payload.get("Date", "")
        start_str = payload.get("start_time", "")
        end_str = payload.get("end_time", "")
        status = payload.get("status", "")
        calendar_id = payload.get("calendar_id", "")
        event_id = payload.get("event_id", "")
        contact_id = payload.get("contact_id", "")
        Note = payload.get("Notes", "")
        pop_up = payload.get("pop_up", "")



        od = openDentalApi(clinic_id)
        pat = PatientService(db, od, clinic_id)
        result = await pat.resolve_patnum(pat=patient_data, contact_id=contact_id)
        if result is None:
            raise ValueError("No contact id could not be resolved ")

        pat_id, pat_num = result
        logger.info("Resolved patient identifiers", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "pat_id": pat_id,
            "pat_num": pat_num,
        })

        appointment_req = AppointmentRequest(
            date_str=date_str,
            start_str=start_str,
            end_str=end_str,
            status=status,
            calendar_id=calendar_id,
            event_id=event_id,
            contact_id=contact_id,
            Note=Note,
            pop_up=pop_up,
            commslog=commslog,
            pat_Num=pat_num,
            clinic_timezone=timezone,
            pat_id=pat_id,
        )
        logger.info("Appointment request prepared", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "calendar_id": calendar_id,
            "status": status,
            "date_str": date_str,
            "start_str": start_str,
            "end_str": end_str,
        })

        appointment_service = AppointmentService(db=db, od_client=od, clinic=clinic)
        apt_num = await appointment_service.book(appointment_req)

        if not apt_num:
            logger.error("Appointment Failed to get Booked", extra={
                "clinic": clinic.id,
                "pat_ num": pat_num,
                "contact_id": contact_id,
            })
            raise ValueError("Appointment booking Failed ")

        logger.info("Appointment booked", extra={
            "clinic_id": clinic_id,
            "event_id": event_id,
            "contact_id": contact_id,
            "apt_num": apt_num,
        })

    except circuit_breaker_open_error:
        logger.warning(
            "Too many failures; circuit breaker is open",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_id": payload.get("contact_id"),
            },
        )
        raise ValueError("Opendental is down please try again later")

    except SQLAlchemyError as e:
        db.rollback()
        logger.exception(
            f"Error processing patient: {e}",
            extra={
                "clinic_id": clinic_id,
                "crm_type": crm_type,
                "event_id": payload.get("event_id"),
                "contact_id": payload.get("contact_id"),
            },
        )
        raise HTTPException(status_code=500, detail="Database error occurred")

    finally:
        db.close()
