from __future__ import annotations

from typing import Any

from billing.toroforge.exceptions import ToroForgeValidationError
from billing.toroforge.toroforge_client.client import ToroForgeClient


class ToroForgePaymentClient:
    def __init__(self, client: ToroForgeClient) -> None:
        self.client = client

    async def initialize_payment(
        self,
        *,
        admin: str,
        adminpwd: str,
        currency: str,
        token: str,
        address: str,
        amount: str,
        success_url: str,
        cancel_url: str,
        payment_type: str,
        fee_type: str = "0",
        passthrough: str = "0",
        commission_rate: str = "0",
        exchange: str = "0",
        payer_name: str | None = None,
        payer_address: str | None = None,
        payer_city: str | None = None,
        payer_state: str | None = None,
        payer_country: str | None = None,
        payer_zipcode: str | None = None,
        payer_phone: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "admin": admin,
            "adminpwd": adminpwd,
        }

        params = [
            {"name": "currency", "value": currency},
            {"name": "token", "value": token},
            {"name": "address", "value": address},
            {"name": "amount", "value": amount},
            {"name": "success_url", "value": success_url},
            {"name": "cancel_url", "value": cancel_url},
            {"name": "paymenttype", "value": payment_type},
            {"name": "feetype", "value": fee_type},
            {"name": "passthrough", "value": passthrough},
            {"name": "commissionrate", "value": commission_rate},
            {"name": "exchange", "value": exchange},
        ]

        optional_params = [
            ("payername", payer_name),
            ("payeraddress", payer_address),
            ("payercity", payer_city),
            ("payerstate", payer_state),
            ("payercountry", payer_country),
            ("payerzipcode", payer_zipcode),
            ("payerphone", payer_phone),
            ("description", description),
        ]

        for name, value in optional_params:
            if value:
                params.append({"name": name, "value": value})

        data = await self.client.call_write(
            method="POST",
            path="/api/payment/toro/",
            op="paymentinitialize",
            params=params,
            headers=headers,
        )

        if not isinstance(data, dict):
            raise ToroForgeValidationError(
                f"ToroForge paymentinitialize returned unexpected response: {data}"
            )

        return data

    async def get_payment_by_txid(
        self,
        *,
        admin: str,
        adminpwd: str,
        txid: str,
    ) -> dict[str, Any]:
        normalized_txid = txid.strip()
        if not normalized_txid:
            raise ToroForgeValidationError("txid is required")

        headers = {
            "admin": admin,
            "adminpwd": adminpwd,
        }

        params = [
            {"name": "txid", "value": normalized_txid},
        ]

        data = await self.client.call_read(
            method="POST",
            path="/api/payment/toro/",
            op="getfiattransactions_txid",
            params=params,
            headers=headers,
        )

        if not isinstance(data, dict):
            raise ToroForgeValidationError(
                f"ToroForge getfiattransactions_txid returned unexpected response: {data}"
            )

        records = data.get("data")
        if records is not None and not isinstance(records, list):
            raise ToroForgeValidationError(
                f"ToroForge getfiattransactions_txid returned unexpected data payload: {data}"
            )

        return data
    

    async def get_address_balance(
    self,
    *,
    address: str,
    ) -> dict[str, Any]:
        normalized_address = address.strip()

        if not normalized_address:
            raise ToroForgeValidationError("address is required")

        response = await self.client.call_read(
            path="query",
            op="getaddrbalance",
            params=[
                {"name": "addr", "value": normalized_address},
            ],
            method="GET",
        )

        if response.get("result") is not True:
            message = str(response.get("message") or "").strip()
            raise ToroForgeValidationError(message or "ToroForge address balance query failed")

        return response
