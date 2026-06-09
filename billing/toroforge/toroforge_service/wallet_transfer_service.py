from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from auth.security import decode_secret
from billing.toroforge.exceptions import ToroForgeError, ToroForgeValidationError
from billing.toroforge.money import (
    coerce_amount_decimal,
    to_amount_minor,
    to_provider_amount_string,
)
from billing.toroforge.toroforge_client.wallet_transfer_client import (
    ToroForgeWalletTransferClient,
)
from core.models import (
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentProvider,
    RegisteredClinics,
    Wallet,
    WalletLedgerEntry,
    WalletStatus,
    WalletTransfer,
    WalletTransferStatus,
    WalletType,
)

logger = logging.getLogger(__name__)

class ToroForgeWalletTransferService:
    def __init__(
            self,
            db:Session,
            transfer_client: ToroForgeWalletTransferClient
    ) -> None:
        
        self.db = db
        self.transfer_client = transfer_client 

    
    async def transfer_from_dso_to_clinic(
            self,
            *,
            dso_id: UUID,
            clinic_id: UUID,
            amount: str,
            currency:str,
            idempotency_key:str,
            initiated_by_user_id: UUID

    ) -> dict[str, Any]:
        
        normalized_idempotency_key = idempotency_key.strip()
        if not normalized_idempotency_key:
            raise ToroForgeValidationError("Idempotency-Key is required")
        
        normalized_currency = currency.strip().upper()
        amount_decimal = coerce_amount_decimal(amount)
        amount_minor = to_amount_minor(
            amount = amount_decimal,
            currency = normalized_currency
        )
        provider_amount = to_provider_amount_string(
            amount=amount_decimal,
            currency=normalized_currency
        )

        existing_transfer = (
            self.db.query(WalletTransfer)
            .filter(WalletTransfer.idempotency_key == normalized_idempotency_key)
            .first()
        )

        if existing_transfer:
            return {
                "wallet_transfer_id": str(existing_transfer.id),
                "status": existing_transfer.status.value,
                "external_transaction_id": existing_transfer.external_transaction_id,
                "amount_minor": existing_transfer.amount_minor,
                "currency": existing_transfer.currency,
                "idempotency_key": existing_transfer.idempotency_key,
                "reused": True,
            }
        
        clinic = (
            self.db.query(RegisteredClinics)
            .filter(RegisteredClinics.id == clinic_id)
            .first()
        )

        if not clinic:
            raise ToroForgeValidationError("Clinic not found")
        
        if clinic.dso_id != dso_id:
            raise ToroForgeValidationError(
                "Clinic does not belong to this DSO"
            )
        
        sender_wallet = (
            self.db.query(Wallet)
            .filter(
                Wallet.dso_id == dso_id,
                Wallet.wallet_type == WalletType.DSO_TREASURY,
            )
            .with_for_update()
            .first()
        )

        if not sender_wallet:
            raise ToroForgeValidationError("DSO treasury wallet not found")
        
        receiver_wallet = (
            self.db.query(Wallet)
            .filter(
                Wallet.clinic_id == clinic_id,
                Wallet.wallet_type == WalletType.CLINIC,
            )
            .with_for_update()
            .first()
        )

        if not receiver_wallet:
            raise ToroForgeValidationError("Clinic wallet not found")
        
        self.validate_transfer_wallets(
            sender_wallet=sender_wallet,
            receiver_wallet=receiver_wallet,
            dso_id=dso_id,
            clinic_id=clinic_id,
            currency=normalized_currency,
            amount_minor=amount_minor,
        )

        sender_password = decode_secret(sender_wallet.external_wallet_password_encrypted)
        if not sender_password:
            raise ToroForgeValidationError(
                "DSO treasury wallet does not have an issued password"
            )

        sender_address = sender_wallet.external_wallet_address
        if not sender_address:
            raise ToroForgeValidationError(
                "DSO treasury wallet has no ToroForge address"
            )

        receiver_address = receiver_wallet.external_wallet_address
        if not receiver_address:
            raise ToroForgeValidationError(
                "Clinic wallet has no ToroForge address"
            )

        transfer = WalletTransfer(
            from_wallet_id=sender_wallet.id,
            to_wallet_id=receiver_wallet.id,
            initiated_by_user_id=initiated_by_user_id,
            amount_minor=amount_minor,
            currency=normalized_currency,
            status=WalletTransferStatus.PENDING,
            external_transaction_id=None,
            idempotency_key=normalized_idempotency_key,
            completed_at=None,
            failure_reason=None,
        )

        self.db.add(transfer)
        self.db.flush()

        transaction_group_id = uuid4()


        sender_ledger = WalletLedgerEntry(
            wallet_id=sender_wallet.id,
            counterparty_wallet_id=receiver_wallet.id,
            transaction_group_id=transaction_group_id,
            entry_type=LedgerEntryType.TRANSFER_OUT,
            direction=LedgerDirection.DEBIT,
            status=LedgerStatus.PENDING,
            amount_minor=amount_minor,
            currency=normalized_currency,
            balance_after_minor=None,
            provider=PaymentProvider.TOROFORGE,
            external_transaction_id=None,
            reference_type="wallet_transfer",
            reference_id=str(transfer.id),
            idempotency_key=f"{normalized_idempotency_key}:ledger:debit",
            details={
                "stage": "pending_provider_transfer",
                "from_wallet_id": str(sender_wallet.id),
                "to_wallet_id": str(receiver_wallet.id),
                "provider_amount": provider_amount,
            },
            posted_at=None,
            failure_reason=None,
        )


        receiver_ledger = WalletLedgerEntry(
            wallet_id=receiver_wallet.id,
            counterparty_wallet_id=sender_wallet.id,
            transaction_group_id=transaction_group_id,
            entry_type=LedgerEntryType.TRANSFER_IN,
            direction=LedgerDirection.CREDIT,
            status=LedgerStatus.PENDING,
            amount_minor=amount_minor,
            currency=normalized_currency,
            balance_after_minor=None,
            provider=PaymentProvider.TOROFORGE,
            external_transaction_id=None,
            reference_type="wallet_transfer",
            reference_id=str(transfer.id),
            idempotency_key=f"{normalized_idempotency_key}:ledger:credit",
            details={
                "stage": "pending_provider_transfer",
                "from_wallet_id": str(sender_wallet.id),
                "to_wallet_id": str(receiver_wallet.id),
                "provider_amount": provider_amount,
            },
            posted_at=None,
            failure_reason=None,
        )

        self.db.add(sender_ledger)
        self.db.add(receiver_ledger)

        try:
            provider_response = await self.transfer_client.make_inter_wallet_transfer(
                sender_address=sender_address,
                sender_password=sender_password,
                receiver_address=receiver_address,
                amount=provider_amount,
                currency=normalized_currency,
            )

            external_transaction_id = self.extract_transfer_txid(provider_response)
            completed_at = self.utcnow()
            
            sender_new_balance = sender_wallet.cached_balance_minor - amount_minor
            receiver_new_balance = receiver_wallet.cached_balance_minor + amount_minor

            transfer.status = WalletTransferStatus.COMPLETED
            transfer.external_transaction_id = external_transaction_id
            transfer.completed_at = completed_at
            transfer.failure_reason = None

            sender_ledger.status = LedgerStatus.POSTED
            sender_ledger.balance_after_minor = sender_new_balance
            sender_ledger.external_transaction_id = external_transaction_id
            sender_ledger.posted_at = completed_at
            sender_ledger.details = {
                **(sender_ledger.details or {}),
                "stage": "provider_transfer_completed",
                "provider_response": provider_response,
            }

            receiver_ledger.status = LedgerStatus.POSTED
            receiver_ledger.balance_after_minor = receiver_new_balance
            receiver_ledger.external_transaction_id = external_transaction_id
            receiver_ledger.posted_at = completed_at
            receiver_ledger.details = {
                **(receiver_ledger.details or {}),
                "stage": "provider_transfer_completed",
                "provider_response": provider_response,
            }

            sender_wallet.cached_balance_minor = sender_new_balance
            sender_wallet.last_balance_sync_at = completed_at

            receiver_wallet.cached_balance_minor = receiver_new_balance
            receiver_wallet.last_balance_sync_at = completed_at

            self.db.commit()

            self.db.refresh(transfer)
            self.db.refresh(sender_ledger)
            self.db.refresh(receiver_ledger)
            self.db.refresh(sender_wallet)
            self.db.refresh(receiver_wallet)


            return {
                "wallet_transfer_id": str(transfer.id),
                "sender_ledger_entry_id": str(sender_ledger.id),
                "receiver_ledger_entry_id": str(receiver_ledger.id),
                "from_wallet_id": str(sender_wallet.id),
                "to_wallet_id": str(receiver_wallet.id),
                "status": transfer.status.value,
                "amount_minor": amount_minor,
                "currency": normalized_currency,
                "external_transaction_id": external_transaction_id,
                "sender_new_cached_balance_minor": sender_wallet.cached_balance_minor,
                "receiver_new_cached_balance_minor": receiver_wallet.cached_balance_minor,
                "provider_response": provider_response,
                "reused": False,
            }

        except Exception as exc:
            self.db.rollback()

            self.mark_transfer_failed_safely(
                transfer_id=transfer.id,
                reason=str(exc),
            )

            raise

    
    def validate_transfer_wallets(
        self,
        *,
        sender_wallet: Wallet,
        receiver_wallet: Wallet,
        dso_id: UUID,
        clinic_id: UUID,
        currency: str,
        amount_minor: int,
    ) -> None:
        if sender_wallet.wallet_type != WalletType.DSO_TREASURY:
            raise ToroForgeValidationError(
                "Only a DSO treasury wallet can initiate transfers"
            )

        if receiver_wallet.wallet_type != WalletType.CLINIC:
            raise ToroForgeValidationError(
                "Transfers can only be sent to clinic wallets"
            )

        if sender_wallet.dso_id != dso_id:
            raise ToroForgeValidationError(
                "Sender wallet does not belong to this DSO"
            )

        if receiver_wallet.clinic_id != clinic_id:
            raise ToroForgeValidationError(
                "Receiver wallet does not belong to this clinic"
            )

        if receiver_wallet.dso_id and receiver_wallet.dso_id != dso_id:
            raise ToroForgeValidationError(
                "Receiver clinic wallet does not belong to this DSO"
            )

        if sender_wallet.status != WalletStatus.ACTIVE:
            raise ToroForgeValidationError("DSO treasury wallet is not active")

        if receiver_wallet.status != WalletStatus.ACTIVE:
            raise ToroForgeValidationError("Clinic wallet is not active")

        if not sender_wallet.external_wallet_address:
            raise ToroForgeValidationError(
                "DSO treasury wallet has no ToroForge address"
            )

        if not receiver_wallet.external_wallet_address:
            raise ToroForgeValidationError(
                "Clinic wallet has no ToroForge address"
            )

        if not sender_wallet.external_wallet_password_encrypted:
            raise ToroForgeValidationError(
                "DSO treasury wallet has no encrypted password"
            )

        if sender_wallet.currency.strip().upper() != currency:
            raise ToroForgeValidationError(
                "DSO treasury wallet currency does not match transfer currency"
            )

        if receiver_wallet.currency.strip().upper() != currency:
            raise ToroForgeValidationError(
                "Clinic wallet currency does not match transfer currency"
            )

        if sender_wallet.cached_balance_minor < amount_minor:
            raise ToroForgeValidationError(
                "DSO treasury wallet has insufficient cached balance"
            )

    
    def extract_transfer_txid(self, payload: dict[str, Any]) -> str | None:
        records = payload.get("data")
           
        if not isinstance(records, list) or not records:
            return None

        first_record = records[0]

        if not isinstance(first_record, dict):
            return None

        txid = first_record.get("TX_ID")

        if txid is None:
            return None

        normalized_txid = str(txid).strip()

        return normalized_txid or None
    


    
    def mark_transfer_failed_safely(
        self,
        *,
        transfer_id: UUID,
        reason: str,
    ) -> None:
        
        try:
            transfer = (
                self.db.query(WalletTransfer)
                .filter(WalletTransfer.id == transfer_id)
                .first()
            )

            if not transfer:
                return

            if transfer.status == WalletTransferStatus.COMPLETED:
                return

            transfer.status = WalletTransferStatus.FAILED
            transfer.failure_reason = reason

            ledgers = (
                self.db.query(WalletLedgerEntry)
                .filter(
                    WalletLedgerEntry.reference_type == "wallet_transfer",
                    WalletLedgerEntry.reference_id == str(transfer.id),
                )
                .all()
            )

            for ledger in ledgers:
                if ledger.status != LedgerStatus.POSTED:
                    ledger.status = LedgerStatus.FAILED
                    ledger.failure_reason = reason
                    ledger.details = {
                        **(ledger.details or {}),
                        "stage": "provider_transfer_failed",
                        "failure_reason": reason,
                    }

            self.db.commit()

        except SQLAlchemyError:
            self.db.rollback()
            logger.exception(
                "Failed to mark wallet transfer as failed",
                extra={"wallet_transfer_id": str(transfer_id)},
            )

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)











