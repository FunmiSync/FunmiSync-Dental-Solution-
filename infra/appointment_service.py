from sdk.opendental_sdk import openDentalApi
from fastapi import Depends
from  sqlalchemy.orm  import Session
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from core.models import Appointments
from core.schemas import AppointmentRequest, Appointments_create, Appointments_update, create_commslogs, create_pop_ups
import logging 
from core.utils import check_time_slot, opendental_get_operatory_status, opendental_pattern_time_build

logger = logging.getLogger(__name__)


class AppointmentService():
    def __init__(self, db: Session, clinic , od_client: openDentalApi) : 
        self.od = od_client
        self.db = db
        self.clinic = clinic

    async def book (self, req: AppointmentRequest ):
        reserve =   await self.book_reserve(req)
        existing_aptnum = reserve.AptNum if (reserve and reserve.AptNum ) else None 

        if reserve and existing_aptnum:
            change = bool(
            reserve.status != req.status
            or reserve.date != req.date_str
            or reserve.start_time != req.start_str
            or str(reserve.end_time) != str(req.end_str)
            )

            if not change:
                return existing_aptnum

        operatories = await self.get_operatories(req)

        if not operatories:
            logger.warning("No Operatories found for this Calendar ")
            return None 
    
        start_dt, end_dt , pattern = await self.build_time(req)

        AptNum = await self.book_into_operatory( req, operatories = operatories , start_dt = start_dt , end_dt = end_dt, pattern = pattern, AptNum  = existing_aptnum)

        if not AptNum:
            logger.warning("No timeslot available for this Appointment in any operatory")
            return None 
        
        logger.info(f"Appointment is being booked for {AptNum} in {operatories} for clinic {self.clinic.clinic_name}")
        if reserve:
            reserve.AptNum = int(AptNum)
            reserve.status = req.status   # type: ignore
            reserve.date = req.date_str   # type: ignore
            reserve.start_time = start_dt   # type: ignore
            reserve.end_time = end_dt    # type: ignore
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


    async def book_into_operatory(self,  req: AppointmentRequest, operatories: list , start_dt, end_dt,  pattern : str  ,  AptNum : int | None   ):
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
            
            AptNum = created["AptNum"]
            return AptNum
        return None 
    


    async def book_reserve(self, req:AppointmentRequest):
        if  not req.event_id:
            return None 

        row = self.db.query(Appointments).filter_by(clinic_id = self.clinic.id , event_id = req.event_id).first()
        if row:
            return row 
        
        row = Appointments(
        clinic_id=self.clinic.id,
        event_id=req.event_id,
        status=req.status,
        start_time=req.start_str,   
        end_time=req.end_str,
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



    async def create_appointment( self , req: AppointmentRequest, pattern, op, start_dt):
        appointment = Appointments_create(
            PatNum= req.pat_Num,
            Pattern = pattern,
            AptDateTime= start_dt, 
            Op = op,
            Note = req.Note,
            AptStatus = req.status
        )
        return await  self.od.create_appointments( appointment_data = appointment)
    
    async def update_appointment(self, req: AppointmentRequest, pattern, AptNum, op, start_dt): 
        appointment = Appointments_update(
            Pattern= pattern,
            AptDateTime= start_dt,
            Op = op,
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


    async def  get_operatories(self, req: AppointmentRequest):
        return (
            await opendental_get_operatory_status(self.clinic, req.status, req.calendar_id)
        ) or [] 
    

    async def build_time(self, req: AppointmentRequest):
        return (
            await  opendental_pattern_time_build(req.date_str, req.start_str, req.end_str, req.clinic_timezone)
        )
    

