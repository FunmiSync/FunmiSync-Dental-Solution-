from __future__ import annotations
from typing import Any
from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
from app.infrastructure.providers.toroforge.client import ToroForgeClient



class ToroForgeKYCClient:
    def __init__(self, client: ToroForgeClient) -> None:
        self.client = client

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








