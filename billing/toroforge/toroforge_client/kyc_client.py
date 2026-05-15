from __future__ import annotations
from typing import Any
from billing.toroforge.exceptions import ToroForgeValidationError
from billing.toroforge.toroforge_client.client import ToroForgeClient



class ToroForgeKYCClient:
    def __init__(self, client: ToroForgeClient) -> None:
        self.client = client

    
    async def check_kyc(
            self,
            *,
            first_name: str,
            middle_name: str | None,
            last_name: str,
            bvn: str,
            currency: str,
            phone_number: str,
            dob: str,
            address: str,
    ) -> dict[str, Any]:

        normalized_first_name = first_name.strip()
        normalized_last_name = last_name.strip()
        normalized_middle_name  = middle_name.strip() if middle_name else ""
        normalized_bvn = bvn.strip()
        normalized_bvn = bvn.strip()
        normalized_currency = currency.strip().upper()
        normalized_phone_number = phone_number.strip()
        normalized_dob = dob.strip()
        normalized_address = address.strip()

        if not normalized_first_name:
            raise ToroForgeValidationError("first_name is required")
        if not normalized_last_name:
            raise ToroForgeValidationError("last_name is required")
        if not normalized_bvn:
            raise ToroForgeValidationError("bvn is required")
        if not normalized_currency:
            raise ToroForgeValidationError("currency is required")
        if not normalized_phone_number:
            raise ToroForgeValidationError("phone_number is required")
        if not normalized_dob:
            raise ToroForgeValidationError("dob is required")
        if not normalized_address:
            raise ToroForgeValidationError("address is required")
        

        headers = {
            "admin": self.client.config.admin,
            "adminpwd": self.client.config.adminpwd
        }

        params = [
            {"name": "currency", "value": normalized_currency},
            {"name": "bvn", "value": normalized_bvn},
            {"name": "firstName", "value": normalized_first_name},
            {"name": "lastName", "value": normalized_last_name},
            {"name": "middleName", "value": normalized_middle_name},
            {"name": "phoneNumber", "value": normalized_phone_number},
            {"name": "dob", "value": normalized_dob},
            {"name": "address", "value": normalized_address},
        ]

        data = await self.client.call_write(
            method= "POST",
            path="/payment/toro",
            op="check_kyc",
            params=params,
            headers=headers,
            base_url=self.client.config.connectw_url
        )

        if not isinstance(data, dict):
            raise ToroForgeValidationError(
                f"ToroForge check_kyc returned unexpected response: {data}"
            )
        
        if data.get("result") is False:
            error_message = data.get("error") or data.get("message") or "KYC check failed"
            raise ToroForgeValidationError(str(error_message))
        
        return data


    
    async def check_address_verified(
            self,
            *,
            address: str,
    ) -> dict[str, Any]:
        
        normalized_address = address.strip()

        if  not normalized_address:
            raise ToroForgeValidationError("address is required")

        data = await self.client.request_json(
            method="POST",
            path="/api/verified/check-kyc",
            json_body= {"address": normalized_address},
            base_url=self.client.config.connectw_url
        )

        verified = data.get("verified")
        provider = data.get("provider")

        if not isinstance(verified, bool):
            raise ToroForgeValidationError(
                f"ConnectW check-kyc returned unexpected response: {data}"
            )
        
        if provider is not None and not isinstance(provider, str):
            raise ToroForgeValidationError(
                f"ConnectW check-kyc returned invalid provider field: {data}"
            )
        
        return {
            "verified": verified,
            "provider": provider,
        }





