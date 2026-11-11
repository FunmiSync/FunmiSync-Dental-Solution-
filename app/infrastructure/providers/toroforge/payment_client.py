from __future__ import annotations

from typing import Any

from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.exceptions import DefinitiveRejection


class ToroForgePaymentClient:
    def __init__(self, client: ToroForgeClient) -> None:
        self.client = client

    async def initialize_payment(
        self,
        *,
        admin: str,
        adminpwd: str,
        currency: str,
        token: str | None,
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

        if token:
            params.insert(1, {"name": "token", "value": token})

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
            base_url=self.client.config.payment_url,
        )

        if not isinstance(data, dict):
            raise ToroForgeValidationError(
                f"ToroForge paymentinitialize returned unexpected response: {data}"
            )

        if data.get("result") is False:
            raise DefinitiveRejection("Provider rejected funding initialization")
        if data.get("result") is not True:
            message = str(
                data.get("message")
                or data.get("error")
                or ""
            ).strip()

            raise ToroForgeValidationError(
                message or f"ToroForge paymentinitialize failed: {data}"
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
            base_url=self.client.config.payment_url,
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
    

    async def get_payments_for_address(
        self,
        *,
        admin: str,
        adminpwd: str,
        address: str,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        """Fiat payments made to this wallet address between two dates (YYYY-MM-DD, inclusive)."""
        normalized_address = address.strip()
        if not normalized_address:
            raise ToroForgeValidationError("address is required")

        data = await self.client.call_read(
            method="POST",
            path="/api/payment/toro/",
            op="getfiattransactions_address_range",
            params=[
                {"name": "addr", "value": normalized_address},
                {"name": "startdate", "value": start_date},
                {"name": "enddate", "value": end_date},
            ],
            headers={"admin": admin, "adminpwd": adminpwd},
            base_url=self.client.config.payment_url,
        )

        records = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get("result") is not True or not isinstance(records or [], list):
            raise ToroForgeValidationError("ToroForge getfiattransactions_address_range query failed")
        return [record for record in records or [] if isinstance(record, dict)]

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
