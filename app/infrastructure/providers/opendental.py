from app.models.entities import RegisteredClinics
import httpx
from app.infrastructure.providers.payloads import patient_payload, appointment_payload, appointment_payload_update, create_commlog, create_pops, retry_with_bak_off
from app.infrastructure.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError
from app.schemas.api import patient_model, Appointments_create, Appointments_update, create_commslogs, create_pop_ups
from uuid import UUID
from app.security.encryption import decode_secret
from functools import lru_cache

class OpenDentalAuthError(Exception):
    pass

class OpenDentalUnavailableError(ConnectionError):
    pass

class OpenDentalRequestError(ValueError):
    pass


@lru_cache(maxsize=1024)
def clinic_breaker(clinic_id: UUID) -> CircuitBreaker:
    """A clinic's invalid credentials must not trip other clinics' breakers."""
    return CircuitBreaker(max_failures=5, reset_timeout=30, half_open_max_calls=1,
                          name=f"opendental:{clinic_id}")

class openDentalApi:
    cb = CircuitBreaker(
        max_failures=5,
        reset_timeout= 30,
        half_open_max_calls= 1,
        name= "opendental"
    )

    def __init__(self, clinic: RegisteredClinics) -> None:
        # The caller loads the clinic in its own (async) session; no I/O happens here.
        if clinic.is_disabled:
            raise OpenDentalAuthError("Clinic is disabled")
        try:
            self.developer_key = decode_secret(clinic.od_developer_key)
            self.customer_key = decode_secret(clinic.od_customer_key)
        except Exception as exc:
            raise OpenDentalAuthError("OpenDental credentials cannot be decrypted") from exc
        if not self.developer_key or not self.customer_key:
            raise OpenDentalAuthError("OpenDental credentials are missing")
        self.clinic_num = clinic.clinic_number
        self.cb = clinic_breaker(clinic.id)
        self.base_url = "https://api.opendental.com/api/v1"
        self.headers = {
            "Authorization": f"ODFHIR {self.developer_key}/{self.customer_key}",
            "Content-Type": "application/json"
        }

    async def _request(self, method:str , endpoint:str, **kwargs):
        url = f"{self.base_url}{endpoint}"
        if not self.cb.allow_request():
             raise CircuitBreakerOpenError ("Circuit breaker OPEN: Too many recent failures. Try again later")

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
           # A timed-out write may already have succeeded remotely. Do not replay it.
           response = await retry_with_bak_off(send) if method.upper() == "GET" else await send()
           self.cb.success()
           return response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                self.cb.success()
                raise OpenDentalAuthError("OpenDental rejected clinic credentials") from exc
            if exc.response.status_code in {408, 429} or exc.response.status_code >= 500:
                self.cb.on_failure()
                raise OpenDentalUnavailableError("OpenDental is temporarily unavailable") from exc
            self.cb.success()
            raise OpenDentalRequestError("OpenDental rejected the request; check clinic mapping and appointment data") from exc
        except httpx.RequestError as exc:
            self.cb.on_failure()
            raise OpenDentalUnavailableError("OpenDental connection failed") from exc
    
                 
    
    async def search_patients(self, last_name: str, date_of_birth: str | None):
        birth_date = date_of_birth or ""
        return await self._request("GET", "/patients", params={"Lname": last_name, "BirthDate": birth_date})

    async def create_patient(self, patient_data: patient_model):
        endpoint = f"/patients"
        body = patient_payload(patient_data)
        return  await self._request("POST", endpoint, json=body)

    async def create_patients(self, patient_data: patient_model):
        return await self.create_patient(patient_data)
    
    async def get_appointments_in_operatory(self, operatory: int | str, dateStart: str, dateEnd: str):
        endpoint = f"/appointments?Op={operatory}&dateStart={dateStart}&dateEnd={dateEnd}"
        return await self._request("GET", endpoint)
    
    async def create_appointments(self, appointment_data: Appointments_create ):
        endpoint = f"/appointments"
        body = appointment_payload(appointment_data) 
        return  await self._request("POST", endpoint, json=body)
    

    async def update_appointment(self, Aptnum: int | str, appointment_data: Appointments_update):
        endpoint = f"/appointments/{Aptnum}"
        body = appointment_payload_update(appointment_data)
        return await self._request("PUT", endpoint, json =body )
    
    async def  create_commslog(self, comms_logs:create_commslogs):
        endpoint = f"/commlogs"
        body = create_commlog(comms_logs)
        return await self._request("POST", endpoint, json=body)
    
    async def  create_pops(self,  pops:create_pop_ups ):
        endpoint = f"/popups"
        body = create_pops(pops)
        return await self._request("POST", endpoint, json=body)
        
    
