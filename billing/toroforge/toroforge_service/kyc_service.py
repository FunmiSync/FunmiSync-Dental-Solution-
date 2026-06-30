from __future__ import annotations 
import logging
from typing import Any 
from billing.toroforge.toroforge_client.kyc_client import ToroForgeKYCClient
from billing.toroforge.exceptions import ToroForgeValidationError
from core.models import Wallet
from uuid import UUID
from auth.security import decode_secret
from urllib.parse import quote
from sqlalchemy.orm import Session, load_only

logger = logging.getLogger(__name__)


class ToroForgeKYCService:
    def __init__(self, db: Session, kyc_client: ToroForgeKYCClient) -> None:
         self.db = db
         self.kyc_client = kyc_client
      
    
    async def get_address_verification_status(
            self,
            *,
            address: str
    ) -> dict[str, Any]:
        
        log_ctx = {
            "address_suffix": self._mask_address(address),
        }


        logger.info("ToroForge address verification requested", extra=log_ctx)


        response = await self.kyc_client.check_address_verified(address=address)
       
        
        logger.info(
            "ToroForge address verification completed",
            extra={
                **log_ctx,
                "verified": response["verified"],
                "provider": response.get("provider"),
            },
        )

        return response
    

    async def check_wallet_kyc_status(self, *, wallet_id: UUID) -> tuple[dict[str, Any], bool]:
        wallet = (
            self.db.query(Wallet)
            .options(
                load_only(
                    Wallet.id,
                    Wallet.external_wallet_address,
                    Wallet.kyc_verified,
                )
            )
            .filter(Wallet.id == wallet_id)
            .first()
        )
        if not wallet:
            raise ToroForgeValidationError("Wallet not found")
        if not wallet.external_wallet_address:
            raise ToroForgeValidationError("Wallet has no external address")

        status = await self.get_address_verification_status(
            address=wallet.external_wallet_address
        )

        wallet.kyc_verified = bool(status["verified"])

        try:
            self.db.commit()
            self.db.refresh(wallet)
        except Exception:
            self.db.rollback()
            raise

        return status, bool(wallet.kyc_verified)
    
    

    async def get_wallet_kyc_link(
            self, 
            *,
            wallet_id: UUID
    ) -> tuple[str, str]:
        
        wallet = self.db.query(Wallet).filter(Wallet.id == wallet_id).first()

        if not wallet:
            raise ToroForgeValidationError("Wallet not found")

        password = decode_secret(wallet.external_wallet_password_encrypted)
        
        if not wallet.external_wallet_address:
            raise ToroForgeValidationError("Wallet has no external address")
        
        if not password:
            raise ToroForgeValidationError("Wallet has no external password")
        
        address = wallet.external_wallet_address.strip()

        kyc_url = (
            f"{self.kyc_client.client.config.connectw_url.rstrip('/')}"
            f"/KYC/project-verify?address={quote(address)}"
        )
        

        return (kyc_url, password)
        

    def _mask_address(self, address: str) -> str:
        normalized = address.strip()
        if len(normalized) <= 8:
            return normalized
        return normalized[-8:]



        




        

