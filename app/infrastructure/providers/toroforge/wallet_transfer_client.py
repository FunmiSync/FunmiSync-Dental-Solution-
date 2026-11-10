from __future__ import annotations
from typing import Any
from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.exceptions import DefinitiveRejection


class ToroForgeWalletTransferClient:
    def __init__(self, client: ToroForgeClient):
        self.client = client

    async def make_inter_wallet_transfer(
        self,
        *,
        sender_address: str,
        sender_password: str,
        receiver_address: str,
        amount: str,
        currency: str 
    ) -> dict[str, Any]:
        
        normalized_currency = currency.strip().upper()

        currency_config = {
            "USD": "dollar"
        }

        currency_value = currency_config.get(normalized_currency)

        if not currency_value:
            raise ToroForgeValidationError(
                f"Unsupported transfer currency: {currency}"
            )

        response = await self.client.call_write(
            method = "POST",
            path = f"/currency/{currency_value}",
            op="transfer",
            params=[
                {"name": "client", "value": sender_address.strip()},
                {"name": "clientpwd", "value": sender_password},
                {"name": "to", "value": receiver_address.strip()},
                {"name": "val", "value": amount},
            ],
        ) 

        if not isinstance(response, dict):
            raise ToroForgeValidationError(
                f"ToroForge transfer returned unexpected response: {response}"
            )
        
        if response.get("result") is False:
            raise DefinitiveRejection("Provider rejected wallet transfer")
        if response.get("result") is not True:
            message = str(
                response.get("error")
                or response.get("message")
                or ""
            ).strip()

            raise ToroForgeValidationError(
                message or "ToroForge wallet transfer failed"
            )

        return response
        

    

