"""Who may view, manage, fund or create a ToroForge wallet.

These guards sit beside app/security/rbac.py and, like it, raise
HTTPException so controllers can call them before any provider work starts.
"""
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models.entities import RegisteredClinics, Wallet
from app.security.rbac import require_clinic_access, require_clinic_manage, require_dso_access, require_dso_manage


def _unlinked_wallet() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Wallet is not linked to a clinic or DSO",
    )


async def get_wallet_for_access_check(*, db: AsyncSession, wallet_id: UUID) -> Wallet:
    # Load only the ownership columns; the full row is loaded later under a lock.
    wallet = await db.scalar(
        select(Wallet).options(load_only(Wallet.id, Wallet.clinic_id, Wallet.dso_id)).where(Wallet.id == wallet_id).limit(1)
    )

    if not wallet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wallet not found")

    if wallet.clinic_id is None and wallet.dso_id is None:
        raise _unlinked_wallet()

    return wallet


async def require_wallet_view_access(*, db: AsyncSession, user_id: UUID, wallet: Wallet) -> None:
    if wallet.clinic_id is not None:
        await require_clinic_access(db=db, user_id=user_id, clinic_id=wallet.clinic_id)
        return

    if wallet.dso_id is not None:
        await require_dso_access(db=db, user_id=user_id, dso_id=wallet.dso_id)
        return

    raise _unlinked_wallet()


async def require_wallet_manage_access(*, db: AsyncSession, user_id: UUID, wallet: Wallet) -> None:
    """KYC and password changes: standalone clinic or DSO treasury wallets only."""
    if wallet.clinic_id is not None and wallet.dso_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="KYC for clinics under a DSO must be completed at the DSO level",
        )

    if wallet.clinic_id is not None:
        await require_clinic_manage(db=db, user_id=user_id, clinic_id=wallet.clinic_id)
        return

    if wallet.dso_id is not None:
        await require_dso_manage(db=db, user_id=user_id, dso_id=wallet.dso_id)
        return

    raise _unlinked_wallet()


async def require_wallet_funding_access(*, db: AsyncSession, user_id: UUID, wallet: Wallet) -> None:
    """Funding: a standalone clinic manages its own wallet; any DSO-linked wallet needs DSO management."""
    if wallet.clinic_id is not None and wallet.dso_id is None:
        await require_clinic_manage(db=db, user_id=user_id, clinic_id=wallet.clinic_id)
        return

    if wallet.dso_id is not None:
        await require_dso_manage(db=db, user_id=user_id, dso_id=wallet.dso_id)
        return

    raise _unlinked_wallet()


async def require_standalone_clinic_wallet_create_access(*, db: AsyncSession, user_id: UUID, clinic_id: UUID) -> RegisteredClinics:
    clinic = await require_clinic_manage(db=db, user_id=user_id, clinic_id=clinic_id)

    if clinic.dso_id is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clinic belongs to a DSO. Use the DSO clinic wallet endpoint.",
        )

    return clinic


async def require_dso_clinic_wallet_create_access(*, db: AsyncSession, user_id: UUID, dso_id: UUID, clinic_id: UUID) -> RegisteredClinics:
    clinic = await require_clinic_manage(db=db, user_id=user_id, clinic_id=clinic_id)

    if clinic.dso_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Clinic is not under a DSO. Use the standalone clinic wallet endpoint.",
        )

    if clinic.dso_id != dso_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinic does not belong to this DSO.",
        )

    return clinic
