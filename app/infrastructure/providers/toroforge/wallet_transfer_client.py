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

    async def get_dollar_transactions(self, *, address: str, count: int) -> list[dict[str, Any]]:
        """Newest-first dollar transactions touching this address, read from the Toronet query API.

        Each record carries EV_Hash, EV_From, EV_To, EV_Value (in dollars) and EV_Time (ISO, UTC).
        """
        normalized_address = address.strip()
        if not normalized_address:
            raise ToroForgeValidationError("address is required")

        response = await self.client.call_read(
            method="GET",
            path="query",
            op="getaddrtransactions_dollar",
            params=[
                {"name": "addr", "value": normalized_address},
                {"name": "count", "value": count},
            ],
        )

        records = response.get("data") if isinstance(response, dict) else None
        if not isinstance(response, dict) or response.get("result") is not True or not isinstance(records or [], list):
            raise ToroForgeValidationError("ToroForge transaction history query failed")
        return [record for record in records or [] if isinstance(record, dict)]
