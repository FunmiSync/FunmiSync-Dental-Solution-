from core.database import SessionLocal
from fastapi import HTTPException, status
from core.models import RegisteredClinics, Patients
import httpx
from core.utils import patient_payload, appointment_payload, appointment_payload_update, create_commlog, create_pops, retry_with_bak_off
from core.circuti_breaker import circuit_breaker, circuit_breaker_open_error
from core.schemas import patient_model, Appointments_create, Appointments_update, create_commslogs, create_pop_ups
import  json



class openDentalApi:
    cb = circuit_breaker(max_failures=5, reset_timeout=30)

    def __init__(self, clinic_id :str) -> None:
        self.db = SessionLocal()
        clinic =self.db.query(RegisteredClinics).filter_by(id=clinic_id).first()
        if not clinic:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="clinic not found ")
        
        self.developer_key = clinic.od_developer_key
        self.customer_key = clinic.od_customer_key
        self.clinic_num = clinic.clinic_number
        self.base_url = "https://api.opendental.io/api/v1"
        self.headers = {
            "OD-Developer-Key": self.developer_key,
            "OD-Customer-Key": self.customer_key,
            "Content-Type": "application/json"
        }

    
    async def _request(self, method:str , endpoint:str, **kwargs):
        url = f"{self.base_url}{endpoint}"
        if not self.cb.allow_request():
             raise circuit_breaker_open_error ("Circuit breaker OPEN: Too many recent failures. Try again later")

        async def send ():
            if method.upper() == "GET":
                if "params" not in kwargs:
                    kwargs["params"] ={}
                kwargs["params"]["clinicnum"] = self.clinic_num
        
            else:
                if "json" not in kwargs:
                    kwargs["json"] = {}
                kwargs["json"]["ClinicNum"] = self.clinic_num

            async with httpx.AsyncClient(timeout=15.0) as client:
                response =   await client.request(method, url, headers= self.headers, **kwargs)
                response.raise_for_status()
                return response
                
        try:
           response = await retry_with_bak_off(send)
           self.cb.success()
           return response.json()
            
   
        except Exception as e :
            self.cb.on_failure()
            raise
    
                 
    
    async def search_patients(self, last_name:str, date_of_birth: str):
        endpoint = f"/patients?Lname={last_name}&BirthDate={date_of_birth}"
        return await self._request("GET", endpoint)

    async def create_patients(self, patient_data: patient_model ):
        endpoint = f"/patients"
        body =  await patient_payload(patient_data)
        return  await self._request("POST", endpoint, json=body)
    
    async def get_appointments_in_operatory(self, operatory: str , dateStart: str , dateEnd : str ):
        endpoint = f"/appointments?Op={operatory}&dateStart={dateStart}&dateEnd={dateEnd}"
        return await self._request("GET", endpoint)
    
    async def create_appointments(self, appointment_data: Appointments_create ):
        endpoint = f"/appointments"
        body = await appointment_payload(appointment_data) 
        return  await self._request("POST", endpoint, json=body)
    

    async def update_appointment (self,  Aptnum:str, appointment_data: Appointments_update):
        endpoint = f"/appointment/Aptnum={Aptnum}"
        body = await appointment_payload_update(appointment_data)
        return await self._request("PUT", endpoint, json =body )
    
    async def  create_commslog(self, comms_logs:create_commslogs):
        endpoint = f"/commlogs"
        body = await create_commlog(comms_logs)
        return await self._request("POST", endpoint, json=body)
    
    async def  create_pops(self,  pops:create_pop_ups ):
        endpoint = f"/popups"
        body = await create_pops(pops)
        return await self._request("POST", endpoint, json=body)
        
    