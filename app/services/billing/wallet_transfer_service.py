from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.security.encryption import decode_secret, decode_json_secret, encrypt_json_secret, hash_lookup
from app.infrastructure.providers.toroforge.exceptions import ToroForgeError, ToroForgeValidationError
from app.services.billing.money import coerce_amount_decimal, to_amount_minor, to_provider_amount_string, MAX_MINOR
from app.services.external_operations import ExternalOperations, ReconciliationRequired, DefinitiveRejection
from app.services.audit_logs import record_audit
from app.infrastructure.providers.toroforge.wallet_transfer_client import ToroForgeWalletTransferClient
from app.models.entities import LedgerDirection, LedgerEntryType, LedgerStatus, PaymentProvider, RegisteredClinics, Wallet, WalletLedgerEntry, WalletStatus, WalletTransfer, WalletTransferStatus, WalletType

logger = logging.getLogger(__name__)
from app.infrastructure.cache.toroforge_billing_cache import invalidate_billing_scope

class ToroForgeWalletTransferService:
    def __init__(
            self,
            db:Session,
            transfer_client: ToroForgeWalletTransferClient
    ) -> None:
        
        self.db = db
        self.operations = ExternalOperations(db)
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
        if normalized_currency != "USD":
            raise ToroForgeValidationError("Unsupported transfer currency")
        amount_decimal = coerce_amount_decimal(amount)
        amount_minor = to_amount_minor(
            amount = amount_decimal,
            currency = normalized_currency
        )
        provider_amount = to_provider_amount_string(
            amount=amount_decimal,
            currency=normalized_currency
        )

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
            .with_for_update().populate_existing()
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
            .with_for_update().populate_existing()
            .first()
        )

        if not receiver_wallet:
            raise ToroForgeValidationError("Clinic wallet not found")
        
        scoped_key = f"transfer:{dso_id}:{hash_lookup(normalized_idempotency_key)}"
        existing_transfer = self.db.query(WalletTransfer).filter(
            WalletTransfer.idempotency_key.in_([scoped_key, normalized_idempotency_key]),
            WalletTransfer.from_wallet_id == sender_wallet.id,
        ).first()
        if existing_transfer:
            if (existing_transfer.from_wallet_id != sender_wallet.id
                or existing_transfer.to_wallet_id != receiver_wallet.id
                or existing_transfer.amount_minor != amount_minor
                or existing_transfer.currency != normalized_currency):
                raise ToroForgeValidationError("Idempotency key conflicts with an existing transfer")
            if existing_transfer.status == WalletTransferStatus.PENDING:
                operation = self.operations.get(f"wallet-transfer:{existing_transfer.id}")
                if operation and operation.state == "succeeded":
                    return self.post_confirmed_transfer(existing_transfer.id, decode_json_secret(operation.result_encrypted), reused=True)
            return self.transfer_result(existing_transfer, reused=True)

        self.validate_transfer_wallets(
            sender_wallet=sender_wallet, receiver_wallet=receiver_wallet,
            dso_id=dso_id, clinic_id=clinic_id,
            currency=normalized_currency, amount_minor=amount_minor,
        )
        pending = self.db.query(WalletTransfer).filter(
            WalletTransfer.from_wallet_id == sender_wallet.id,
            WalletTransfer.status == WalletTransferStatus.PENDING,
        ).first()
        if pending:
            raise ReconciliationRequired("A pending treasury transfer requires reconciliation")

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
            id=uuid4(),
            from_wallet_id=sender_wallet.id,
            to_wallet_id=receiver_wallet.id,
            initiated_by_user_id=initiated_by_user_id,
            amount_minor=amount_minor,
            currency=normalized_currency,
            status=WalletTransferStatus.PENDING,
            external_transaction_id=None,
            idempotency_key=scoped_key,
            completed_at=None,
            failure_reason=None,
        )

        self.db.add(transfer)

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
            idempotency_key=f"{scoped_key}:ledger:debit",
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
            idempotency_key=f"{scoped_key}:ledger:credit",
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
            self.db.commit()  # intent + both pending ledger entries survive remote failure
        except IntegrityError as exc:
            self.db.rollback()
            raise ReconciliationRequired("Concurrent transfer request; reload the existing operation") from exc

        async def send():
            result = await self.transfer_client.make_inter_wallet_transfer(
                sender_address=sender_address, sender_password=sender_password,
                receiver_address=receiver_address, amount=provider_amount,
                currency=normalized_currency,
            )
            if result.get("result") is not True or not self.extract_transfer_txid(result):
                raise ReconciliationRequired("Transfer response lacks a confirmed transaction reference")
            return result

        try:
            response = await self.operations.execute(
                key=f"wallet-transfer:{transfer.id}", resource_id=sender_wallet.id,
                action="wallet_transfer", request={"to": receiver_wallet.id, "amount_minor": amount_minor,
                                                   "currency": normalized_currency}, call=send,
            )
            return self.post_confirmed_transfer(transfer.id, response, reused=False)
        except DefinitiveRejection:
            self.db.rollback()
            self.mark_transfer_failed_safely(transfer_id=transfer.id, reason="Provider explicitly rejected transfer")
            raise
        except Exception:
            self.db.rollback()
            # The committed pending intent deliberately remains. A different key
            # cannot spend this treasury again until the outcome is reconciled.
            raise

    def transfer_result(self, transfer, *, reused):
        return {"wallet_transfer_id": str(transfer.id), "status": transfer.status.value,
                "external_transaction_id": transfer.external_transaction_id,
                "amount_minor": transfer.amount_minor, "currency": transfer.currency, "reused": reused}

    def post_confirmed_transfer(self, transfer_id, response, *, reused):
        try:
            transfer = self.db.query(WalletTransfer).filter(WalletTransfer.id == transfer_id).with_for_update().populate_existing().first()
            if not transfer:
                raise ToroForgeValidationError("Transfer not found")
            if transfer.status == WalletTransferStatus.COMPLETED:
                return self.transfer_result(transfer, reused=True)
            if transfer.status != WalletTransferStatus.PENDING:
                raise ReconciliationRequired("Transfer is not pending")
            operation_key = f"wallet-transfer:{transfer.id}"
            operation = self.operations.get(operation_key)
            if not operation or operation.state != "succeeded":
                raise ReconciliationRequired("Transfer has no durably confirmed provider result")
            external_id = self.extract_transfer_txid(response)
            if not external_id or response.get("result") is not True:
                raise ReconciliationRequired("Transfer confirmation requires reconciliation")
            from app.services.billing.settlement import is_settled
            records = response.get("data")
            if not records or not is_settled(records[0]):
                raise ReconciliationRequired("Transfer acknowledgement lacks verified settlement status")
            wallets = self.db.query(Wallet).filter(Wallet.id.in_([transfer.from_wallet_id, transfer.to_wallet_id])).order_by(Wallet.id).with_for_update().populate_existing().all()
            by_id = {wallet.id: wallet for wallet in wallets}
            sender, receiver = by_id[transfer.from_wallet_id], by_id[transfer.to_wallet_id]
            new_sender = sender.cached_balance_minor - transfer.amount_minor
            new_receiver = receiver.cached_balance_minor + transfer.amount_minor
            if new_sender < 0 or new_receiver > MAX_MINOR:
                raise ReconciliationRequired("Transfer balance requires reconciliation")
            ledgers = self.db.query(WalletLedgerEntry).filter(
                WalletLedgerEntry.reference_type == "wallet_transfer",
                WalletLedgerEntry.reference_id == str(transfer.id),
            ).with_for_update().populate_existing().all()
            expected = {(sender.id, LedgerDirection.DEBIT), (receiver.id, LedgerDirection.CREDIT)}
            if (len(ledgers) != 2
                or {(entry.wallet_id, entry.direction) for entry in ledgers} != expected
                or any(entry.status != LedgerStatus.PENDING or entry.amount_minor != transfer.amount_minor
                       or entry.currency != transfer.currency for entry in ledgers)
                or ledgers[0].transaction_group_id != ledgers[1].transaction_group_id):
                raise ReconciliationRequired("Transfer ledger pair requires reconciliation")
            now = self.utcnow()
            transfer.status = WalletTransferStatus.COMPLETED
            transfer.completed_at = now
            transfer.external_transaction_id = external_id
            transfer.failure_reason = None
            sender.cached_balance_minor, receiver.cached_balance_minor = new_sender, new_receiver
            sender.last_balance_sync_at = receiver.last_balance_sync_at = now
            for ledger in ledgers:
                ledger.status = LedgerStatus.POSTED
                ledger.posted_at = now
                ledger.external_transaction_id = external_id
                ledger.balance_after_minor = new_sender if ledger.wallet_id == sender.id else new_receiver
                ledger.details = {**(ledger.details or {}), "stage": "provider_transfer_completed",
                                  "provider_response_encrypted": encrypt_json_secret(response)}
            self.operations.mark_applied(operation_key)
            record_audit(self.db,clinic_id=receiver.clinic_id,entity_type="wallet_transfer",entity_id=transfer.id,
                         action="completed",details={"actor_user_id":str(transfer.initiated_by_user_id)})
            self.db.commit()
            invalidate_billing_scope(clinic_id=receiver.clinic_id, dso_id=sender.dso_id)
            debit = next(entry for entry in ledgers if entry.wallet_id == sender.id)
            credit = next(entry for entry in ledgers if entry.wallet_id == receiver.id)
            return {**self.transfer_result(transfer, reused=reused),
                    "sender_ledger_entry_id": str(debit.id), "receiver_ledger_entry_id": str(credit.id),
                    "from_wallet_id": str(sender.id), "to_wallet_id": str(receiver.id),
                    "sender_new_cached_balance_minor": new_sender, "receiver_new_cached_balance_minor": new_receiver,
                    "provider_response": response}
        except Exception:
            self.db.rollback()
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











