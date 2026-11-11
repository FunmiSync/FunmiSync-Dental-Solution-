"""FastAPI dependencies that build ToroForge billing services per request.

Each dependency opens one provider HTTP client, hands it to the service,
and closes it when the request finishes.
"""
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database import get_db
from app.infrastructure.providers.toroforge.client import ToroForgeClient
from app.infrastructure.providers.toroforge.config import get_toroforge_config
from app.infrastructure.providers.toroforge.keystore_client import ToroForgeKeyStoreClient
from app.infrastructure.providers.toroforge.kyc_client import ToroForgeKYCClient
from app.infrastructure.providers.toroforge.payment_client import ToroForgePaymentClient
from app.infrastructure.providers.toroforge.tns_client import ToroForgeTNSClient
from app.infrastructure.providers.toroforge.wallet_transfer_client import ToroForgeWalletTransferClient
from app.services.billing.funding_service import ToroForgeFundingService
from app.services.billing.kyc_service import ToroForgeKYCService
from app.services.billing.wallet_service import ToroForgeWalletService
from app.services.billing.wallet_transfer_service import ToroForgeWalletTransferService


@asynccontextmanager
async def _toroforge_client() -> AsyncIterator[ToroForgeClient]:
    client = ToroForgeClient(config=get_toroforge_config(), breaker=ToroForgeClient.toroforge_breaker)
    try:
        yield client
    finally:
        await client.aclose()


async def get_toroforge_wallet_service(db: AsyncSession = Depends(get_db)) -> AsyncIterator[ToroForgeWalletService]:
    async with _toroforge_client() as client:
        yield ToroForgeWalletService(
            db=db,
            keystore_client=ToroForgeKeyStoreClient(client),
            tns_client=ToroForgeTNSClient(client),
        )


async def get_toroforge_kyc_service(db: AsyncSession = Depends(get_db)) -> AsyncIterator[ToroForgeKYCService]:
    async with _toroforge_client() as client:
        yield ToroForgeKYCService(db=db, kyc_client=ToroForgeKYCClient(client))


async def get_toroforge_funding_service(db: AsyncSession = Depends(get_db)) -> AsyncIterator[ToroForgeFundingService]:
    async with _toroforge_client() as client:
        yield ToroForgeFundingService(db=db, payment_client=ToroForgePaymentClient(client))


async def get_toroforge_wallet_transfer_service(db: AsyncSession = Depends(get_db)) -> AsyncIterator[ToroForgeWalletTransferService]:
    async with _toroforge_client() as client:
        yield ToroForgeWalletTransferService(db=db, transfer_client=ToroForgeWalletTransferClient(client))
