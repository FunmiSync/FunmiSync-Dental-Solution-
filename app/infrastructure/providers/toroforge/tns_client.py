from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.exceptions import ToroForgeDuplicateNameError, ToroForgeValidationError

class ToroForgeTNSClient:
    def __init__(self, client: ToroForgeClient)-> None:
        self.client = client

    async def get_address_for_name(self, *, username: str) -> str | None:
        data = await self.client.call_read(method="GET", path="/tns", op="getaddr",
                                          params=[{"name": "name", "value": username}])
        address = data.get("address") if data.get("result") is True else None
        return address.strip() if isinstance(address, str) and address.strip() else None
    
    async def is_name_used(self, *, username: str) -> bool:
        data = await self.client.call_read(
            method= "GET",
            path="/tns",
            op="isnameused",
            params=[
                {"name": "name", "value": username},
            ]
        )

        if "isused" not in data:
            raise ToroForgeValidationError(
                f"ToroForge isnameused response missing isused: {data}"
            )
        
        if not isinstance(data["isused"], bool):
            raise ToroForgeValidationError("ToroForge returned an invalid name availability result")
        return data["isused"]
    

    async def assert_name_available(self, *, username: str ) -> None:
        is_used = await self.is_name_used(username=username)
        if is_used:
            raise ToroForgeDuplicateNameError("Toroforge username is already taken")
        
    
    async def set_name(self, *, address: str, password: str, username: str)-> None:
        data = await self.client.call_write(
           method="POST",
            path="/tns/cl",
            op="setname",
            params=[
                {"name": "client", "value": address},
                {"name": "clientpwd", "value": password},
                {"name": "name", "value": username},
            ] 
        )

        if data.get("result") is not True:
             raise ToroForgeValidationError("ToroForge name registration was not confirmed")


