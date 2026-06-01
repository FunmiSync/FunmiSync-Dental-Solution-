from __future__ import annotations
import logging 
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import UUID
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from billing.toroforge.exceptions import ToroForgeError, ToroForgeValidationError
from billing.toroforge.toroforge_client.payment_client import ToroForgePaymentClient
from core.models import (
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentProvider,
    PaymentTransaction,
    PaymentTransactionStatus,
    Wallet,
    WalletLedgerEntry,
    WalletStatus
)

logger = logging.getLogger(__name__)


class ToroForgeFundingService:
    def __init__(
            self,
            db:Session,
            payment_client: ToroForgePaymentClient
    ) -> None:
        
        self.db = db
        self.payment_client = payment_client


    async def initialize_wallet_funding(
        self,
        *,
        wallet_id: UUID,
        request_id: str,
        amount: str,
        currency: str,
        payment_type: str,
        success_url: str,
        cancel_url: str,
        token: str | None = None,
        payer_name: str | None = None,
        payer_address: str | None = None,
        payer_city: str | None = None,
        payer_state: str | None = None,
        payer_country: str | None = None,
        payer_zipcode: str | None = None,
        payer_phone: str | None = None,
        description: str | None = None,
        ) -> dict[str, Any]:
        
        request_id = request_id.strip()
                
        if not request_id:
            raise ToroForgeValidationError("request_id is required")

        normalized_currency = currency.strip().upper()
        normalized_token = (token or normalized_currency).strip().upper()
        normalized_payment_type = payment_type.strip().lower()
        amount_decimal = self._coerce_amount_decimal(amount)
        amount_minor = self.to_amount_minor(
            amount=amount_decimal,
            currency=normalized_currency,
        )
        provider_amount = self._to_provider_amount_string(
            amount=amount_decimal,
            currency=normalized_currency,
        )


        if normalized_payment_type not in {"card", "bank", "wire"}:
            raise ToroForgeValidationError("Unsupported payment type")
        
        wallet = self.db.query(Wallet).filter(Wallet.id == wallet_id).first()
        if not wallet:
            raise ToroForgeValidationError("Wallet not found")
        
        if  wallet.status  != WalletStatus.ACTIVE:
            raise ToroForgeValidationError("Wallet must be active before funding")
        
        if not wallet.external_wallet_address:
            raise ToroForgeValidationError("Wallet has no external ToroForge address")
        
        if wallet.kyc_verified is not True:
            raise ToroForgeValidationError("Wallet Kyc has not been Verified")

        
        idempotency_key = self.build_funding_idempotency_key(request_id)
        ledger_idempotency_key = f"funding-ledger:{request_id}"

        log_ctx = {
             "wallet_id": str(wallet.id),
            "clinic_id": str(wallet.clinic_id) if wallet.clinic_id else None,
            "dso_id": str(wallet.dso_id) if wallet.dso_id else None,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
            "amount_minor": amount_minor,
            "currency": normalized_currency,
            "payment_type": normalized_payment_type
        }
        
        logger.info("ToroForge funding initialization requested", extra=log_ctx)

        existing_payment = (
            self.db.query(PaymentTransaction).filter(PaymentTransaction.idempotency_key == idempotency_key).first()
        )

        if existing_payment:
            existing_ledger = self.get_ledger_for_payment(existing_payment.id)

            logger.info(
                "ToroForge funding initialization reused existing payment transaction",
                extra={
                    **log_ctx,
                    "payment_transaction_id": str(existing_payment.id),
                    "existing_status": existing_payment.status.value,
                },
            )

            existing_details = existing_payment.details or {}

            return {
                "payment_transaction_id": str(existing_payment.id),
                "ledger_entry_id": str(existing_ledger.id) if existing_ledger else None,
                "status": existing_payment.status.value,
                "external_payment_id": existing_payment.external_payment_id,
                "provider_response": self.extract_provider_response(existing_details),
            }

        payment = PaymentTransaction(
            dso_id = wallet.dso_id or None,
            clinic_id = wallet.clinic_id or None,
            wallet_id=wallet.id,
            subscription_id=None,
            provider= PaymentProvider.TOROFORGE,
            purpose="wallet_funding",
            amount_minor = amount_minor,
            currency = normalized_currency,
            external_payment_id = None,
            status = PaymentTransactionStatus.PENDING,
            idempotency_key= idempotency_key,
            failure_reason = None,
            details={
                "stage": "local_pending_created",
                "request_id": request_id,
                "wallet_address": wallet.external_wallet_address,
                "payment_type": normalized_payment_type,
                "provider_amount": provider_amount,
            },
            succeeded_at = None,
            failed_at = None
        )
        self.db.add(payment)
        self.db.flush()

        pending_ledger = WalletLedgerEntry(
            wallet_id = wallet.id,
            counterparty_wallet_id = None,
            entry_type = LedgerEntryType.TOP_UP,
            direction=  LedgerDirection.CREDIT,
            status=LedgerStatus.PENDING,
            amount_minor = amount_minor,
            currency=  normalized_currency,
            balance_after_minor= None,
            provider = PaymentProvider.TOROFORGE,
            external_transaction_id = None,
            reference_type= "payment_transaction",
            reference_id= str(payment.id),
            idempotency_key = ledger_idempotency_key,
            details={
                "stage": "pending_provider_payment",
                "request_id": request_id,
                "payment_transaction_id": str(payment.id),
                "provider_amount": provider_amount,
            },
            posted_at = None,
            failure_reason=None,
        )

        self.db.add(pending_ledger)

        self.commit_and_refresh(
            payment,
            pending_ledger,
            action= "creating local [ending funding records",
            log_ctx= log_ctx,
            duplicate_error_message="Funding request already exist forn this request_id"
        )

        logger.info(
            "ToroForge local pending payment and ledger created",
            extra={
                **log_ctx,
                "payment_transaction_id": str(payment.id),
                "ledger_entry_id": str(pending_ledger.id),
            },
        )

        try:
            logger.info(
                "ToroForge paymentinitialize started",
                extra={**log_ctx, "payment_transaction_id": str(payment.id)},
            )

            provider_response = await self.payment_client.initialize_payment(
                admin=self.payment_client.client.config.admin,
                adminpwd=self.payment_client.client.config.adminpwd,
                currency=normalized_currency,
                token=normalized_token,
                address=wallet.external_wallet_address,
                amount=provider_amount,
                success_url=success_url,
                cancel_url=cancel_url,
                payment_type=normalized_payment_type,
                payer_name=payer_name,
                payer_address=payer_address,
                payer_city=payer_city,
                payer_state=payer_state,
                payer_country=payer_country,
                payer_zipcode=payer_zipcode,
                payer_phone=payer_phone,
                description=description
            )

        except Exception as exc:
            payment.status = PaymentTransactionStatus.FAILED
            payment.failure_reason = str(exc)
            payment.failed_at = self.utcnow()
            payment.details = {
                **(payment.details or {}),
                "stage": "provider_initialize_failed",
                "failure_reason": str(exc),
            }

            pending_ledger.status = LedgerStatus.FAILED
            pending_ledger.failure_reason = str(exc)
            pending_ledger.details = {
                **(pending_ledger.details or {}),
                "stage": "provider_initialize_failed",
                "failure_reason": str(exc)
            }

            self.commit_and_refresh(
                payment,
                pending_ledger,
                action="persisting failed funding initialization",
                log_ctx={**log_ctx, "payment_transaction_id": str(payment.id)},
            )

            logger.exception(
                "ToroForge funding initialization failed",
                extra={**log_ctx, "payment_transaction_id": str(payment.id)},
            )
            raise
    
        external_payment_id = self.extract_txid(provider_response)
        

        payment.external_payment_id = external_payment_id
        payment.failure_reason = None
        payment.details = {
            **(payment.details or {}),
            "stage": "provider_initialized",
            "provider_amount": provider_amount,
            "provider_response": provider_response,
        }

        pending_ledger.external_transaction_id = external_payment_id
        pending_ledger.details = {
            **(pending_ledger.details or {}),
            "stage": "provider_initialized",
            "provider_response": provider_response,
        }


        self.commit_and_refresh(
            payment,
            pending_ledger,
            action = "saving providerfunding initialization response",
            log_ctx= {
                **log_ctx,
                "payment_transaction_id": str(payment.id),
                "external_payment_id": external_payment_id,
            }
        )

        logger.info(
            "ToroForge funding initialization completed",
            extra={
                **log_ctx,
                "payment_transaction_id": str(payment.id),
                "ledger_entry_id": str(pending_ledger.id),
                "external_payment_id": external_payment_id
            }
        )

        return {
            "payment_transaction_id": str(payment.id),
            "ledger_entry_id": str(pending_ledger.id),
            "status": payment.status.value,
            "external_payment_id": external_payment_id,
            "provider_response": provider_response,
            "amount_minor": amount_minor
        }


    def record_verified_funding_success(
            self,
            *,
            payment_transaction_id: UUID,
            provider_response: dict[str, Any],
            succeeded_at: datetime | None = None
    ) -> dict[str, Any]:
        
        succeeded_at = succeeded_at or self.utcnow()
        try:
            payment = (
                self.db.query(PaymentTransaction).filter(PaymentTransaction.id == payment_transaction_id).with_for_update().first()
            )

            if not payment:
                raise ToroForgeValidationError("Payment transaction not Found")
            
            if not payment.wallet_id:
                raise ToroForgeValidationError("Payment transaction has no wallet reference")
            
            wallet = (
                self.db.query(Wallet).filter(Wallet.id == payment.wallet_id).with_for_update().first()
            ) 

            if not wallet:
                raise ToroForgeValidationError("Wallet not found for payment transaction")
            
            ledger = self.get_ledger_for_payment(payment.id, lock = True)

            if not ledger:
                raise ToroForgeValidationError("Pending ledger entry not found for payment transaction")

            log_ctx = {
                "payment_transaction_id": str(payment.id),
                "wallet_id": str(wallet.id),
                "ledger_entry_id": str(ledger.id),
                "external_payment_id": payment.external_payment_id,
                "amount_minor": payment.amount_minor,
                "currency": payment.currency,
            }

            logger.info("ToroForge funding success reconciliation started", extra=log_ctx)

            if (
                payment.status == PaymentTransactionStatus.SUCCEEDED and ledger.status == LedgerStatus.POSTED
            ):
                logger.info(
                    "ToroForge funding success reconciliation already applied",
                    extra=log_ctx,
                )

                return {
                    "payment_transaction_id": str(payment.id),
                    "ledger_entry_id": str(ledger.id),
                    "wallet_id": str(wallet.id),
                    "new_cached_balance_minor": wallet.cached_balance_minor,
                }
            
            external_payment_id = payment.external_payment_id or self.extract_txid(provider_response)

            if not external_payment_id:
                raise ToroForgeValidationError("Verified success payload is missing txid")
            
            payment.external_payment_id = external_payment_id
            new_balance_minor = wallet.cached_balance_minor + payment.amount_minor

            payment.status = PaymentTransactionStatus.SUCCEEDED
            payment.failure_reason = None
            payment.failed_at = None
            payment.succeeded_at = succeeded_at
            payment.details = {
                **(payment.details or {}),
                "stage": "provider_verified_success",
                "provider_response": provider_response,
            }

            ledger.status = LedgerStatus.POSTED
            ledger.external_transaction_id = external_payment_id
            ledger.balance_after_minor = new_balance_minor
            ledger.posted_at = succeeded_at
            ledger.failure_reason = None
            ledger.details = {
                **(ledger.details or {}),
                "stage": "provider_verified_success",
                "provider_response": provider_response,
            }

            wallet.cached_balance_minor = new_balance_minor
            wallet.last_balance_sync_at = succeeded_at

            self.commit_and_refresh(
                payment,
                wallet,
                ledger,
                action="posting verified funding credit",
                log_ctx=log_ctx,
            )

            logger.info(
                "ToroForge funding success reconciliation completed",
                extra={
                    **log_ctx,
                    "new_cached_balance_minor": wallet.cached_balance_minor,
                },
            )

            return {
                "payment_transaction_id": str(payment.id),
                "ledger_entry_id": str(ledger.id),
                "wallet_id": str(wallet.id),
                "new_cached_balance_minor": wallet.cached_balance_minor,
            }
    
        except Exception:
            self.db.rollback()
            raise


    def record_verified_funding_failure(
            self,
            *,
            payment_transaction_id: UUID,
            reason: str,
            provider_response: dict[str, Any] | None = None
    ) -> None:
        
        try:
            payment = (
                self.db.query(PaymentTransaction).filter(PaymentTransaction.id ==payment_transaction_id).with_for_update().first()
            )

            if not payment:
                 raise ToroForgeValidationError("Payment transaction not found")
            
            ledger = self.get_ledger_for_payment(payment.id, lock= True)
        
            if not ledger:
                raise ToroForgeValidationError("Pending ledger entry not found for payment transaction")
            
            log_ctx = {
                "payment_transaction_id": str(payment.id),
                "wallet_id": str(payment.wallet_id) if payment.wallet_id else None,
                "ledger_entry_id": str(ledger.id),
                "external_payment_id": payment.external_payment_id,
            }

            if (
                payment.status == PaymentTransactionStatus.SUCCEEDED or ledger.status == LedgerStatus.POSTED
            ):
                raise ToroForgeValidationError(
                    "Funding has already been posted successfully. Handle reversal separately."
                )
            
            if (
                payment.status == PaymentTransactionStatus.FAILED and ledger.status == LedgerStatus.FAILED
            ):
                logger.info(
                    "ToroForge funding failure reconciliation already applied",
                    extra={**log_ctx, "reason": reason},
                )
                return
            
            logger.warning(
                "ToroForge funding marked as failed after verification",
                extra={**log_ctx, "reason": reason},
            )

            payment.status = PaymentTransactionStatus.FAILED
            payment.failure_reason = reason
            payment.failed_at = self.utcnow()
            payment.details = {
                **(payment.details or {}),
                "stage": "provider_verified_failed",
                "provider_response": provider_response or {},
                "failure_reason": reason,
            }

            ledger.status = LedgerStatus.FAILED
            ledger.failure_reason = reason
            ledger.details = {
                **(ledger.details or {}),
                "stage": "provider_verified_failed",
                "provider_response": provider_response or {},
                "failure_reason": reason,
            }


            self.commit_and_refresh(
                payment,
                ledger,
                action="saving verified funding failure",
                log_ctx=log_ctx,
            )

        except Exception:
            self.db.rollback()
            raise


    def get_ledger_for_payment(
            self,
            payment_id: UUID,
            *,
            lock: bool = False
    ) -> WalletLedgerEntry | None:
        
        query = (
            self.db.query(WalletLedgerEntry).filter(
                WalletLedgerEntry.reference_type == "payment_transaction",
                WalletLedgerEntry.reference_id == str(payment_id),
                WalletLedgerEntry.entry_type == LedgerEntryType.TOP_UP,
                WalletLedgerEntry.direction == LedgerDirection.CREDIT,
            )
        )

        if lock:
            query = query.with_for_update()

        return query.first()
    


    def commit_and_refresh(
            self,
            *entities: Any,
            action: str,
            log_ctx: dict[str, Any],
            duplicate_error_message: str | None = None
    ) -> None:
        try:
            self.db.commit()
            for entity in entities:
                self.db.refresh(entity)
        
        except IntegrityError as exc:
            self.db.rollback()
            logger.exception(
                "ToroForge funding database integrity error",
                extra={**log_ctx, "action": action},
            )
            if duplicate_error_message:
                raise ToroForgeValidationError(duplicate_error_message) from exc
            raise ToroForgeError(f"Database integrity error while {action}") from exc
        
        except SQLAlchemyError as exc:
            self.db.rollback()
            logger.exception(
                "ToroForge funding database commit failed",
                extra={**log_ctx, "action": action},
            )
            raise ToroForgeError(f"Database error while {action}") from exc
        
    

    def build_funding_idempotency_key(
            self,
            request_id : str
    ) -> str:
        return f"funding:{request_id}"
    

    def _currency_decimals(self, currency: str) -> int:
        decimals_map = {
            "USD": 2,
            "EUR": 2,
            "GBP": 2,
            "NGN": 2,
        }

        decimals = decimals_map.get(currency)
        if decimals is None:
            raise ToroForgeValidationError(f"Unsupported currency: {currency}")

        return decimals
    

    def _normalize_amount(self, *, amount: Decimal, currency: str) -> Decimal:
        decimals = self._currency_decimals(currency)
        quantizer = Decimal("1").scaleb(-decimals)
        return amount.quantize(quantizer, rounding=ROUND_HALF_UP)
    

    def to_amount_minor(self, *, amount, currency: str) -> int:
        decimals = self._currency_decimals(currency)
        normalized_amount = self._normalize_amount(amount=amount, currency=currency)
        multiplier = Decimal(10) ** decimals
        amount_minor = int(normalized_amount * multiplier)

        if amount_minor <= 0:
            raise ToroForgeValidationError("Funding amount must be greater than zero")

        return amount_minor


    def _coerce_amount_decimal(self, amount: str | Decimal | int | float) -> Decimal:
        try:
            decimal_amount = amount if isinstance(amount, Decimal) else Decimal(str(amount).strip())
        except (InvalidOperation, AttributeError, ValueError) as exc:
            raise ToroForgeValidationError("Invalid funding amount") from exc

        if decimal_amount <= 0:
            raise ToroForgeValidationError("Funding amount must be greater than zero")

        return decimal_amount


    def _to_provider_amount_string(self, *, amount: Decimal, currency: str) -> str:
        decimals = self._currency_decimals(currency)
        normalized_amount = self._normalize_amount(amount=amount, currency=currency)
        return f"{normalized_amount:.{decimals}f}"


    def extract_txid(self, payload: dict[str, Any]) -> str | None:
        txid = payload.get("TX_ID")
        if isinstance(txid, str) and txid.strip():
            return txid.strip()

        nested = payload.get("data")
        if isinstance(nested, dict):
            nested_txid = nested.get("TX_ID")
            if isinstance(nested_txid, str) and nested_txid.strip():
                return nested_txid.strip()

        return None
    

    def extract_provider_response(self, details: dict[str, Any]) -> dict[str, Any]:
        provider_response = details.get("provider_response")
        return provider_response if isinstance(provider_response, dict) else {}
    

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)
    
    


    




 


            






 

            










            
                                                                   
