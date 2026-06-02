from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class ToroForgeProvisionWalletResult:
    wallet_id: UUID
    external_wallet_address: str 
    external_wallet_username: str