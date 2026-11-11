from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
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
                "ToroForge createkey response missing address"
            )
        return address
    

    async def verify_key(self, *, address: str, password: str)-> bool:
        data = await self.client.call_read(
            method="GET",
            path="/keystore",
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
            },
        )

        if "result" not in data:
            raise ToroForgeValidationError(
                "ToroForge verifykey response missing result"
            )

        result = data["result"]

        if isinstance(result, bool):
            if result:
                return True
            
            provider_error = "credentials were not verified"
            raise ToroForgeValidationError(
                f"ToroForge verifykey failed: {provider_error}"
            )
            
        raise ToroForgeValidationError(
            "ToroForge verifykey returned unrecognized result"
        )




    async def update_key_password(
        self,
        *,
        address: str,
        old_password :str | None,
        new_password: str
    )-> dict:

        normalized_address = address.strip()

        if not normalized_address:
            raise ToroForgeValidationError("address is required")

        if not old_password:
            raise ToroForgeValidationError("old_password is required")

        if not new_password:
            raise ToroForgeValidationError("new_password is required")
        
        data = await self.client.call_write(
            method="POST",
            path="/keystore",
            op="updatekeypwd",
            params= [
                { "name": "addr", "value": normalized_address },
                { "name": "oldpwd", "value": old_password },
                { "name": "newpwd", "value": new_password },
            ],
        )
        if data.get("result") is not True:
            error = "Failed to update ToroForge key password"
            raise ToroForgeValidationError(str(error))
        return data



