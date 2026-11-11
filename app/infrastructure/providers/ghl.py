import httpx
from app.infrastructure.circuit_breaker import circuit_breaker, circuit_breaker_open_error
from app.infrastructure.providers.payloads import create_contacts, create_appointments, update_appointments
from app.schemas.api import create_contact_ghl, create_appointment_ghl, update_appointment_ghl
from app.infrastructure.providers.payloads import retry_with_bak_off
from typing import Annotated

class GHL():
    cb = circuit_breaker(max_failures= 5 , reset_timeout= 30)

    def __init__ (self, access_token, location_id, sub_account_token):
        self.access_token = access_token
        self.sub_account_token = sub_account_token
        self.location_id = location_id
        self.base_url = "https://services.leadconnectorhq.com"
        self.headers = {
                "Authorization": f"Bearer {self.access_token}" ,
                "Version": "2021-07-28",
                "Content-Type": "application/json",
                "Accept": "application/json"
        }

    async def _request(self, method: str , endpoint : str , **kwargs):
        url = f"{self.base_url}{endpoint}"
        if not self.cb.allow_request():
            raise circuit_breaker_open_error("Too Many Failures Wait for sometime")
        
        async def send():
            async with httpx.AsyncClient(timeout= 15 ) as client:
                response = await client.request(
                    method = method.upper(),
                    url = url,
                   headers = self.headers ,
                   **kwargs,
                )
                response.raise_for_status()
                return response
        try:
            response = await retry_with_bak_off(send) if method.upper() == "GET" else await send()
            self.cb.success()
            return response.json()
        except Exception:
            self.cb.on_failure()
            raise
    
 
    async def search_contact_by_phone(self, phone: str, ): 
        return await self._request("GET", "/contacts", params={"phone": phone, "locationId": self.location_id})
    
    async def create_contact(self, contact_data: create_contact_ghl ):
        endpoint = f"/contacts"
        body =  create_contacts(data = contact_data)
        return await self._request("POST", endpoint, json = body )
    
    async def create_appointments(self, appointment_data : create_appointment_ghl ):
        endpoint = f"/calendars/events/appointments"
        body = create_appointments(appt_data = appointment_data)
        return await self._request("POST", endpoint, json = body )
    
    async def update_appointments(self,  appointment_data: update_appointment_ghl, event_Id : str ):
        endpoint =f"/calendars/events/appointments/{event_Id}" 
        body = update_appointments(appt_data= appointment_data)
        return await self._request("PUT", endpoint, json = body)

