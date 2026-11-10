from __future__ import annotations
import logging 
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.providers.toroforge.exceptions import ToroForgeError, ToroForgeValidationError
from app.infrastructure.providers.toroforge.payment_client import ToroForgePaymentClient
from app.models.entities import LedgerDirection, LedgerEntryType, LedgerStatus, PaymentProvider, PaymentTransaction, PaymentTransactionStatus, Wallet, WalletLedgerEntry, WalletStatus
from app.services.billing.money import coerce_amount_decimal, to_amount_minor, to_provider_amount_string, extract_address_balance_minor

logger = logging.getLogger(__name__)
from app.infrastructure.cache.toroforge_billing_cache import invalidate_billing_scope
from app.services.external_operations import request_fingerprint, ExternalOperations, DefinitiveRejection, ReconciliationRequired
from app.security.encryption import hash_lookup, encrypt_json_secret, decode_json_secret
from app.services.billing.money import MAX_MINOR
from app.services.audit_logs import record_audit


class ToroForgeFundingService:
    def __init__(
            self,
            db: AsyncSession,
            payment_client: ToroForgePaymentClient
    ) -> None:
        
        self.db = db
        self.payment_client = payment_client
        self.operations = ExternalOperations(db)


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
        normalized_token = token.strip().upper() if token and token.strip() else None
        normalized_payment_type = payment_type.strip().lower()
        amount_decimal = coerce_amount_decimal(amount)
        amount_minor = to_amount_minor(
            amount=amount_decimal,
            currency=normalized_currency,
        )
        provider_amount = to_provider_amount_string(
            amount=amount_decimal,
            currency=normalized_currency,
        )


        if normalized_payment_type not in {"card", "bank"}:
            raise ToroForgeValidationError("Unsupported payment type")
        
        wallet = await self.db.scalar(select(Wallet).filter(Wallet.id == wallet_id).with_for_update().execution_options(populate_existing=True).limit(1))
        if not wallet:
            raise ToroForgeValidationError("Wallet not found")
        
        if  wallet.status  != WalletStatus.ACTIVE:
            raise ToroForgeValidationError("Wallet must be active before funding")
        
        if not wallet.external_wallet_address:
            raise ToroForgeValidationError("Wallet has no external ToroForge address")
        
        if wallet.kyc_verified is not True:
            raise ToroForgeValidationError("Wallet Kyc has not been Verified")

        if normalized_currency != wallet.currency.strip().upper() or normalized_currency not in {"USD", "NGN"}:
            raise ToroForgeValidationError("Funding currency must match the wallet and support verification")

        
        idempotency_key = f"funding:{wallet_id}:{hash_lookup(request_id)}"
        ledger_idempotency_key = f"funding-ledger:{wallet_id}:{hash_lookup(request_id)}"
        fingerprint = request_fingerprint(dict(wallet_id=wallet_id, amount_minor=amount_minor,
            currency=normalized_currency, token=normalized_token, payment_type=normalized_payment_type,
            success_url=success_url, cancel_url=cancel_url, payer_name=payer_name, payer_address=payer_address,
            payer_city=payer_city, payer_state=payer_state, payer_country=payer_country,
            payer_zipcode=payer_zipcode, payer_phone=payer_phone, description=description))

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
            await self.db.scalar(select(PaymentTransaction).filter(
                PaymentTransaction.wallet_id == wallet_id,
                or_(PaymentTransaction.idempotency_key == idempotency_key,
                    PaymentTransaction.idempotency_key == self.build_funding_idempotency_key(request_id)),
            ).limit(1))
        )

        if existing_payment:
            if (existing_payment.wallet_id != wallet_id or existing_payment.amount_minor != amount_minor
                or existing_payment.currency != normalized_currency
                or (existing_payment.details or {}).get("request_fingerprint") != fingerprint):
                raise ToroForgeValidationError("Idempotency key conflicts with an existing funding request")
            existing_ledger = await self.get_ledger_for_payment(existing_payment.id)

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

        unresolved = await self.db.scalar(select(PaymentTransaction).filter(
            PaymentTransaction.wallet_id == wallet_id,
            PaymentTransaction.status == PaymentTransactionStatus.PENDING,
            PaymentTransaction.external_payment_id.is_(None),
        ).limit(1))
        if unresolved:
            raise ReconciliationRequired("A funding initialization requires reconciliation before another request")

        payment = PaymentTransaction(
            id=uuid4(),
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
                "request_fingerprint": fingerprint,
                "request_id": request_id,
                "wallet_address": wallet.external_wallet_address,
                "payment_type": normalized_payment_type,
                "provider_amount": provider_amount,
            },
            succeeded_at = None,
            failed_at = None
        )
        self.db.add(payment)

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

        await self.commit_and_refresh(
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
            confirmed_failure = isinstance(exc, DefinitiveRejection)
            payment.status = PaymentTransactionStatus.FAILED if confirmed_failure else PaymentTransactionStatus.PENDING
            payment.failure_reason = "Provider initialization rejected" if confirmed_failure else "Provider result requires reconciliation"
            payment.failed_at = self.utcnow() if confirmed_failure else None
            payment.details = {
                **(payment.details or {}),
                "stage": "provider_initialize_failed" if confirmed_failure else "provider_initialize_uncertain",
                "failure_reason": payment.failure_reason,
            }

            pending_ledger.status = LedgerStatus.FAILED if confirmed_failure else LedgerStatus.PENDING
            pending_ledger.failure_reason = payment.failure_reason
            pending_ledger.details = {
                **(pending_ledger.details or {}),
                "stage": "provider_initialize_failed",
                "failure_reason": payment.failure_reason
            }
          

            await self.commit_and_refresh(
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
        ####on success 
        payment.external_payment_id = external_payment_id
        payment.failure_reason = None
        payment.details = {
            **(payment.details or {}),
            "stage": "provider_initialized",
            "provider_amount": provider_amount,
            "provider_response_encrypted": encrypt_json_secret(provider_response),
        }

        pending_ledger.external_transaction_id = external_payment_id
        pending_ledger.details = {
            **(pending_ledger.details or {}),
            "stage": "provider_initialized",
            "provider_response_encrypted": encrypt_json_secret(provider_response),
        }


        await self.commit_and_refresh(
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


    async def verify_wallet_deposit(
            self,
            *,
            wallet_id: UUID,
            payment_transaction_id: UUID,
            txid: str
    ) -> dict[str, Any]:
        
        normalized_txid =  txid.strip()

        if not normalized_txid:
            raise ToroForgeValidationError("txid is required")
        
        payment = (
            await self.db.scalar(select(PaymentTransaction)
            .filter(
                PaymentTransaction.id == payment_transaction_id,
                PaymentTransaction.wallet_id == wallet_id,
            ).limit(1))
        )

        if not payment:
         raise ToroForgeValidationError("Payment transaction not found")
        
        wallet = await self.db.scalar(select(Wallet).filter(Wallet.id == wallet_id).limit(1))

        if not wallet:
            raise ToroForgeValidationError("Wallet not found")
        
        if wallet.status != WalletStatus.ACTIVE:
            raise ToroForgeValidationError("Wallet must be active before verifying deposit")

        external_wallet_address = wallet.external_wallet_address
        if not external_wallet_address:
            raise ToroForgeValidationError("Wallet has no external ToroForge address")
        
        provider_response = await self.payment_client.get_payment_by_txid(
        admin=self.payment_client.client.config.admin,
        adminpwd=self.payment_client.client.config.adminpwd,
        txid=normalized_txid
        )

        if provider_response.get("result") is not True:
            message = str(provider_response.get("message") or "").strip()
            raise ToroForgeValidationError(message or "ToroForge transaction query failed")
        
        records = provider_response.get("data") or []

        if not records:
            raise ToroForgeValidationError("Deposit transaction was not found on ToroForge")

        matched_record = self.find_matching_fiat_transaction(
            records=records,
            payment=payment,
            wallet=wallet,
            txid=normalized_txid,
        )

        if not matched_record:
            raise ToroForgeValidationError(
                "Deposit transaction does not match this funding request"
            )
        
        if wallet.currency.strip().upper() != payment.currency.strip().upper():
            raise ToroForgeValidationError("Wallet currency does not match payment currency")

        posted = await self.record_verified_funding_success(
                payment_transaction_id=payment_transaction_id,
                provider_response={
                    **provider_response,
                    "matched_record": matched_record,
                },
            )

        return {
            **posted,
            "status": PaymentTransactionStatus.SUCCEEDED.value,
            "currency": payment.currency,
            "txid": normalized_txid,
            "provider_response": provider_response,
        }
    


    def find_matching_fiat_transaction(
        self,
        *,
        records: list[dict[str, Any]],
        payment: PaymentTransaction,
        wallet: Wallet,
        txid: str,
    ) -> dict[str, Any] | None:
        
        expected_currency = payment.currency.strip().upper()
        expected_address = (wallet.external_wallet_address or "").strip().lower()
        expected_txid = (payment.external_payment_id or "").strip()
        if not expected_txid or expected_txid != txid.strip() or not expected_address:
            return None
        for record in records:
            if not isinstance(record, dict):
                continue
            if str(record.get("TX_ID") or "").strip() != expected_txid:
                continue
            if str(record.get("TX_Currency") or "").strip().upper() != expected_currency:
                continue
            if str(record.get("TX_Address") or "").strip().lower() != expected_address:
                continue
            try:
                amount = self.extract_record_amount_minor(record=record, currency=expected_currency)
            except ToroForgeValidationError:
                continue
            if amount != payment.amount_minor:
                continue
            return record
        return None


    def extract_record_amount_minor(
        self,
        *,
        record: dict[str, Any],
        currency: str,
    ) -> int | None:
        raw_amount = (
            record.get("TX_Amount")
        )

        if raw_amount is None:
            return None

        return to_amount_minor(
            amount=coerce_amount_decimal(raw_amount),
            currency=currency,
        )



    async def record_verified_funding_success(
            self,
            *,
            payment_transaction_id: UUID,
            provider_response: dict[str, Any],
            succeeded_at: datetime | None = None
    ) -> dict[str, Any]:
        
        succeeded_at = succeeded_at or self.utcnow()
        try:
            payment = (
                await self.db.scalar(select(PaymentTransaction).filter(PaymentTransaction.id == payment_transaction_id).with_for_update().execution_options(populate_existing=True).limit(1))
            )

            if not payment:
                raise ToroForgeValidationError("Payment transaction not Found")
            
            if not payment.wallet_id:
                raise ToroForgeValidationError("Payment transaction has no wallet reference")
            
            wallet = (
                await self.db.scalar(select(Wallet).filter(Wallet.id == payment.wallet_id).with_for_update().execution_options(populate_existing=True).limit(1))
            ) 

            if not wallet:
                raise ToroForgeValidationError("Wallet not found for payment transaction")
            
            ledger = await self.get_ledger_for_payment(payment.id, lock = True)

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
            
            if (payment.status != PaymentTransactionStatus.PENDING or ledger.status != LedgerStatus.PENDING
                or ledger.wallet_id != wallet.id or ledger.direction != LedgerDirection.CREDIT
                or ledger.amount_minor != payment.amount_minor or ledger.currency != payment.currency):
                raise ReconciliationRequired("Funding ledger requires reconciliation")
            external_payment_id = payment.external_payment_id or self.extract_txid(provider_response)

            if not external_payment_id:
                raise ToroForgeValidationError("Verified success payload is missing txid")
            record = provider_response.get("matched_record")
            if provider_response.get("result") is not True or not record or not self.find_matching_fiat_transaction(
                records=[record], payment=payment, wallet=wallet, txid=external_payment_id):
                raise ToroForgeValidationError("Deposit lacks a successful provider response with matching transaction details")
            
            payment.external_payment_id = external_payment_id
            new_balance_minor = wallet.cached_balance_minor + payment.amount_minor
            if new_balance_minor > MAX_MINOR:
                raise ToroForgeValidationError("Wallet balance exceeds supported limits")

            payment.status = PaymentTransactionStatus.SUCCEEDED
            payment.failure_reason = None
            payment.failed_at = None
            payment.succeeded_at = succeeded_at
            payment.details = {
                **(payment.details or {}),
                "stage": "provider_verified_success",
                "provider_response_encrypted": encrypt_json_secret(provider_response),
            }

            ledger.status = LedgerStatus.POSTED
            ledger.external_transaction_id = external_payment_id
            ledger.balance_after_minor = new_balance_minor
            ledger.posted_at = succeeded_at
            ledger.failure_reason = None
            ledger.details = {
                **(ledger.details or {}),
                "stage": "provider_verified_success",
                "provider_response_encrypted": encrypt_json_secret(provider_response),
            }

            wallet.cached_balance_minor = new_balance_minor
            wallet.last_balance_sync_at = succeeded_at

            await self.commit_and_refresh(
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
            await self.db.rollback()
            raise


    async def record_verified_funding_failure(
            self,
            *,
            payment_transaction_id: UUID,
            reason: str,
            provider_response: dict[str, Any] | None = None
    ) -> None:
        
        try:
            payment = (
                await self.db.scalar(select(PaymentTransaction).filter(PaymentTransaction.id ==payment_transaction_id).with_for_update().limit(1))
            )

            if not payment:
                 raise ToroForgeValidationError("Payment transaction not found")
            
            ledger = await self.get_ledger_for_payment(payment.id, lock= True)
        
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
                "provider_response_encrypted": encrypt_json_secret(provider_response or {}),
                "failure_reason": reason,
            }

            ledger.status = LedgerStatus.FAILED
            ledger.failure_reason = reason
            ledger.details = {
                **(ledger.details or {}),
                "stage": "provider_verified_failed",
                "provider_response_encrypted": encrypt_json_secret(provider_response or {}),
                "failure_reason": reason,
            }


            await self.commit_and_refresh(
                payment,
                ledger,
                action="saving verified funding failure",
                log_ctx=log_ctx,
            )

        except Exception:
            await self.db.rollback()
            raise


    async def get_ledger_for_payment(
            self,
            payment_id: UUID,
            *,
            lock: bool = False
    ) -> WalletLedgerEntry | None:
        
        query = (
            select(WalletLedgerEntry).where(
                WalletLedgerEntry.reference_type == "payment_transaction",
                WalletLedgerEntry.reference_id == str(payment_id),
                WalletLedgerEntry.entry_type == LedgerEntryType.TOP_UP,
                WalletLedgerEntry.direction == LedgerDirection.CREDIT,
            )
        )

        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)

        return await self.db.scalar(query.limit(1))
    


    async def commit_and_refresh(
            self,
            *entities: Any,
            action: str,
            log_ctx: dict[str, Any],
            duplicate_error_message: str | None = None
    ) -> None:
        try:
            for entity in entities:
                if isinstance(entity, PaymentTransaction):
                    record_audit(self.db,clinic_id=entity.clinic_id,entity_type="payment",entity_id=entity.id,
                                 action=entity.status.value,details={"wallet_id":str(entity.wallet_id)})
            await self.db.commit()
            for entity in entities:
                await self.db.refresh(entity)
            scopes = {(entity.clinic_id, entity.dso_id) for entity in entities
                      if isinstance(entity, (Wallet, PaymentTransaction))}
            for clinic_id, dso_id in scopes:
                await invalidate_billing_scope(clinic_id=clinic_id, dso_id=dso_id)
        
        except IntegrityError as exc:
            await self.db.rollback()
            logger.exception(
                "ToroForge funding database integrity error",
                extra={**log_ctx, "action": action},
            )
            if duplicate_error_message:
                raise ToroForgeValidationError(duplicate_error_message) from exc
            raise ToroForgeError(f"Database integrity error while {action}") from exc
        
        except SQLAlchemyError as exc:
            await self.db.rollback()
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
    


    def extract_txid(self, payload: dict[str, Any]) -> str | None:
        txid = payload.get("TX_ID")
        if isinstance(txid, str) and txid.strip():
            return txid.strip()

        matched_record = payload.get("matched_record")
        if isinstance(matched_record, dict):
            matched_txid = matched_record.get("TX_ID")
            if isinstance(matched_txid, str) and matched_txid.strip():
                return matched_txid.strip()

        nested = payload.get("data")
        if isinstance(nested, dict):
            nested_txid = nested.get("TX_ID")
            if isinstance(nested_txid, str) and nested_txid.strip():
                return nested_txid.strip()

        if isinstance(nested, list):
            for record in nested:
                if not isinstance(record, dict):
                    continue
                nested_txid = record.get("TX_ID")
                if isinstance(nested_txid, str) and nested_txid.strip():
                    return nested_txid.strip()

        return None
    

    def extract_provider_response(self, details: dict[str, Any]) -> dict[str, Any]:
        provider_response = decode_json_secret(details["provider_response_encrypted"]) if details.get("provider_response_encrypted") else details.get("provider_response")
        return provider_response if isinstance(provider_response, dict) else {}
    

    def utcnow(self) -> datetime:
        return datetime.now(timezone.utc)
    
    


    




 


            






 

            










            
                                                                   
