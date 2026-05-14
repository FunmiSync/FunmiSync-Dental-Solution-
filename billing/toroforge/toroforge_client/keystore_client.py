from billing.toroforge.toroforge_client.client import ToroForgeClient
from billing.toroforge.exceptions import ToroForgeValidationError
import logging 


logger = logging.getLogger(__name__)

class ToroForgeKeyStoreClient:
    def __init__(self, client: ToroForgeClient) -> None:
        self.client = client

    
    async def create_keystore(self, *, password: str) -> str:
        data = await self.client.call_write(
            method="POST",
            path="/keystore",
            op="createkey",
            params=[
                {"name": "pwd", "value": password}
            ]
        )

        address = data.get("address")
        if not address:
            raise ToroForgeValidationError(
                f"ToroForge createkey response missing address: {data}"
            )
        return address
    

    async def verify_key(self, *, address: str, password: str)-> bool:
        data = await self.client.call_write(
            method="POST",
            path="/keystore/",
            op="verifykey",
            params=[
                {"name": "addr", "value": address},
                {"name": "pwd", "value": password},
            ],
        )

        logger.info(
            "ToroForge verifykey response received",
            extra={
                "address_suffix": address[-8:] if len(address) > 8 else address,
                "provider_response": data,
            },
        )

        if "result" not in data:
            raise ToroForgeValidationError(
                f"ToroForge verifykey response missing result: {data}"
            )

        result = data["result"]

        if isinstance(result, bool):
            if result:
                return True
            
            provider_error = data.get("error") or data.get("message") or str(data)
            raise ToroForgeValidationError(
                f"ToroForge verifykey failed: {provider_error}"
            )
            
        raise ToroForgeValidationError(
            f"ToroForge verifykey returned unrecognized result: {data}"
        )