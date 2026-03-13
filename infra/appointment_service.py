from sdk.opendental_sdk import openDentalApi
from fastapi import Depends
from  sqlalchemy.orm  import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from core.models import Appointments, RegisteredClinics
from core.schemas import AppointmentRequest, Appointments_create, Appointments_update, create_commslogs, create_pop_ups
from datetime import datetime, timezone
import logging 
from typing import Any
from core.utils import check_time_slot, fmt, opendental_get_operatory_status, opendental_pattern_time_build

logger = logging.getLogger(__name__)


class AppointmentService():
    def __init__(self, db: Session, clinic: RegisteredClinics, od_client: openDentalApi) : 
        self.od = od_client
        self.db = db
        self.clinic = clinic

    def _utcnow(self) -> datetime:
        return datetime.now(timezone.utc)

    def _apply_status_transition(self, row: Appointments, new_status: str):
        if row.status == new_status:
            return

        row.previous_status = row.status
        row.status = new_status
        row.status_changed_at = self._utcnow()

    def _format_od_datetime(self, value: datetime) -> str:
        return value.strftime(fmt)

    async def book(self, req: AppointmentRequest) -> int | None:
        start_dt, end_dt , pattern = await self.build_time(req)
        start_text = self._format_od_datetime(start_dt)
        end_text = self._format_od_datetime(end_dt)

        reserve = await self.book_reserve(req, start_text=start_text, end_text=end_text)
        existing_aptnum = reserve.AptNum if (reserve and reserve.AptNum ) else None 

        if reserve and existing_aptnum:
            change = bool(
            reserve.status != req.status
            or reserve.date != req.date_str
            or reserve.start_time != start_text
            or reserve.end_time != end_text
            )

            if not change:
                return existing_aptnum

        operatories = await self.get_operatories(req)

        if not operatories:
            logger.warning("No Operatories found for this Calendar ")
            return None 

        AptNum = await self.book_into_operatory( req, operatories = operatories , start_dt = start_dt , end_dt = end_dt, pattern = pattern, AptNum  = existing_aptnum)

        if not AptNum:
            logger.warning("No timeslot available for this Appointment in any operatory")
            return None 
        
        logger.info(f"Appointment is being booked for {AptNum} in {operatories} for clinic {self.clinic.clinic_name}")
        if reserve:
            reserve.AptNum = int(AptNum)
            self._apply_status_transition(reserve, req.status)
            reserve.date = req.date_str   # type: ignore
            reserve.start_time = start_text   # type: ignore
            reserve.end_time = end_text    # type: ignore
            self.db.commit()

        
        if reserve and not reserve.commslog_done and req.commslog:   # type: ignore
            logger.info(f"commslog is being created for Aptnum {AptNum} ")
            await self.handle_commslog(req)
            reserve.commslog_done = True
            self.db.commit()

        if  reserve and not reserve.popups_done and req.pop_up:   # type: ignore
            logger.info(f"popups is being created for Aptnum {AptNum} ")
            await self.handle_popups(req)

        return AptNum


    async def book_into_operatory(
        self,
        req: AppointmentRequest,
        operatories: list[int],
        start_dt: datetime,
        end_dt: datetime,
        pattern: str,
        AptNum: int | None,
    ) -> int | None:
        #Create date pattern for date time start and date time end
        date_start = start_dt.strftime("%Y-%m-%d")
        date_end = end_dt.strftime("%Y-%m-%d")

        for op in operatories: 
            existing = await self.od.get_appointments_in_operatory(op, date_start, date_end)
        
        
            if not await check_time_slot(existing, start_dt, end_dt):
                continue

            if AptNum: 
                logger.info(f"Updated Appointment for Aptnum {AptNum} in  Op  {op}")
                await self.update_appointment( 
                    pattern = pattern, 
                    req = req ,
                    op = op,  
                    start_dt = start_dt,
                    AptNum= AptNum
                    )
                
                return AptNum
            
            logger.info(f"creating  Appointment for Aptnum {AptNum} in  Op  {op}")
            created = await self.create_appointment(
                    pattern = pattern,
                    op = op,
                    req = req,  
                    start_dt = start_dt 
                )
            
            created_aptnum = created.get("AptNum")
            if created_aptnum is None:
                return None
            AptNum = int(created_aptnum)
            return AptNum
        return None 
    


    async def book_reserve(self, req: AppointmentRequest, start_text: str, end_text: str) -> Appointments | None:
        if  not req.event_id:
            return None 

        row = self.db.query(Appointments).filter_by(clinic_id = self.clinic.id , event_id = req.event_id).first()
        if row:
            return row 
        
        row = Appointments(
        clinic_id=self.clinic.id,
        event_id=req.event_id,
        status=req.status,
        previous_status=None,
        status_changed_at=self._utcnow(),
        start_time=start_text,
        end_time=end_text,
        date=req.date_str,
        calendar_id=req.calendar_id,
        pat_id=req.pat_id,          
        AptNum=None,               
         )
        
        try:
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return row 
        
        except IntegrityError:
            self.db.rollback()
            return (
            self.db.query(Appointments)
            .filter_by(clinic_id=self.clinic.id, event_id=req.event_id)
            .first()
        )

        except SQLAlchemyError:
        # Anything else is a real DB issue — stop
            self.db.rollback()
            raise

    async def create_appointment(
        self,
        req: AppointmentRequest,
        pattern: str,
        op: int,
        start_dt: datetime,
    ) -> dict[str, Any]:
        appointment = Appointments_create(
            PatNum= req.pat_Num,
            Pattern = pattern,
            AptDateTime=self._format_od_datetime(start_dt),
            Op = str(op),
            Note = req.Note,
            AptStatus = req.status
        )
        return await  self.od.create_appointments( appointment_data = appointment)
    
    async def update_appointment(
        self,
        req: AppointmentRequest,
        pattern: str,
        AptNum: int,
        op: int,
        start_dt: datetime,
    ) -> dict[str, Any]:
        appointment = Appointments_update(
            Pattern= pattern,
            AptDateTime=self._format_od_datetime(start_dt),
            Op = str(op),
            AptStatus= req.status
        )

        return  await self.od.update_appointment(Aptnum= AptNum, appointment_data = appointment)
    
    async def handle_commslog(self, req : AppointmentRequest):
        if not req.commslog:
            return 
        
        logs = create_commslogs(
            commlogs = req.commslog,
            PatNum = req.pat_Num 
        )
        await self.od.create_commslog(comms_logs = logs )
    

    
    async def handle_popups(self, req: AppointmentRequest):
        if not req.pop_up:
            return 
        
        pops = create_pop_ups(
            pop_ups = req.pop_up,
            PatNum = req.pat_Num
        )

        await self.od.create_pops(pops = pops )


    async def get_operatories(self, req: AppointmentRequest) -> list[int]:
        return (
            await opendental_get_operatory_status(self.clinic, req.status, req.calendar_id)
        ) or [] 
    

    async def build_time(self, req: AppointmentRequest) -> tuple[datetime, datetime, str]:
        return (
            await  opendental_pattern_time_build(req.date_str, req.start_str, req.end_str, req.clinic_timezone)
        )
    

