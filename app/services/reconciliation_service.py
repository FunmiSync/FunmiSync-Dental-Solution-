"""Find and resolve outside actions whose outcome is unknown.

When ToroForge or OpenDental does not answer, ExternalOperations parks the action as
"uncertain" (or leaves it "in_flight" if the process died mid-call) and refuses to
repeat it, so a retry can never pay or book twice. Funding starts whose checkout link
never came back are parked the same way, as pending payments with no provider id.

An operator checks the provider, then records here what really happened:
- happened:  the stored result is reused, so nothing is sent again;
- not done:  the action is closed as failed, and a fresh attempt is allowed.
auto_resolve() does the same from read-only provider checks (reconciliation_checks)
and leaves anything unclear for a person. Every resolution is written to the audit
log with who checked and why.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.cache.toroforge_billing_cache import invalidate_billing_scope
from app.models.entities import (
    ExternalOperation, LedgerDirection, LedgerEntryType, LedgerStatus, Patients, PaymentTransaction,
    PaymentTransactionStatus, Wallet, WalletLedgerEntry, WalletTransfer, WalletTransferStatus,
)
from app.security.encryption import decode_json_secret, encrypt_json_secret, encrypt_secret
from app.services.audit_logs import record_audit
from app.services.billing.funding_service import ToroForgeFundingService
from app.services.billing.wallet_transfer_service import ToroForgeWalletTransferService
from app.services.billing_service import money_amount_string
from app.services.errors import Conflict, InvalidRequest, NotFound, ServiceError
from app.services.reconciliation_checks import HAPPENED, UNCLEAR, WAITING, Finding

logger = logging.getLogger(__name__)

UNKNOWN_STATES = ("in_flight", "uncertain")
# A call younger than this may still be running; resolving it could race the real reply.
STILL_RUNNING = timedelta(minutes=10)

# What the operator reads off the provider when the action did happen. The workflow
# reuses the stored result instead of calling the provider a second time.
SUCCESS_FIELDS: dict[str, tuple[str, ...]] = {
    "wallet_transfer": ("TX_ID",),
    "wallet_keystore": ("address",),
    "wallet_tns": ("address", "username"),
    "wallet_password_rotation": (),
    "patient_create": ("PatNum",),
    "appointment_create": ("AptNum",),
    "commslog": (),
    "popup": (),
    "funding_start": ("TX_ID",),
}
INTEGER_FIELDS = {"PatNum", "AptNum"}

WHERE_TO_CHECK = {
    "wallet_transfer": "ToroForge: did this amount move from the sender address to the receiver address? Note its TX_ID.",
    "wallet_keystore": "ToroForge: was a key created around this time? Its address is needed.",
    "wallet_tns": "ToroForge TNS: is the username registered to the wallet's address?",
    "wallet_password_rotation": "ToroForge: does the wallet accept the new password?",
    "patient_create": "OpenDental: was the patient created? Note the PatNum.",
    "appointment_create": "OpenDental: was the appointment created? Note the AptNum.",
    "commslog": "OpenDental: was the comm log added to the patient?",
    "popup": "OpenDental: was the popup added to the patient?",
    "funding_start": "ToroForge: does a payment exist for this wallet and amount? Usually not; the payer never got a link.",
}


@dataclass
class StuckItem:
    kind: str                      # "operation" or "funding_start"
    id: UUID
    action: str
    state: str
    age: timedelta
    may_still_be_running: bool
    context: dict[str, str] = field(default_factory=dict)

    @property
    def where_to_check(self) -> str:
        return WHERE_TO_CHECK.get(self.action, "Check the provider for this action.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age(created_at: datetime, now: datetime) -> timedelta:
    if created_at.tzinfo is None:  # SQLite drops tzinfo; PostgreSQL keeps it
        created_at = created_at.replace(tzinfo=timezone.utc)
    return now - created_at


def _transfer_id(operation_key: str) -> UUID:
    return UUID(operation_key.split("#", 1)[0].split(":", 1)[1])


# ------------------------------------------------------------------ list

async def list_unknown_outcomes(db: AsyncSession, *, now: datetime | None = None) -> list[StuckItem]:
    now = now or _now()
    items: list[StuckItem] = []

    operations = (await db.scalars(
        select(ExternalOperation).where(ExternalOperation.state.in_(UNKNOWN_STATES)).order_by(ExternalOperation.created_at)
    )).all()
    for operation in operations:
        age = _age(operation.created_at, now)
        items.append(StuckItem(
            kind="operation", id=operation.id, action=operation.action, state=operation.state, age=age,
            may_still_be_running=operation.state == "in_flight" and age < STILL_RUNNING,
            context=await _operation_context(db, operation),
        ))

    payments = (await db.scalars(
        select(PaymentTransaction).where(
            PaymentTransaction.status == PaymentTransactionStatus.PENDING,
            PaymentTransaction.external_payment_id.is_(None),
        ).order_by(PaymentTransaction.created_at)
    )).all()
    for payment in payments:
        age = _age(payment.created_at, now)
        items.append(StuckItem(
            kind="funding_start", id=payment.id, action="funding_start", state="pending", age=age,
            may_still_be_running=age < STILL_RUNNING,
            context={"wallet": str(payment.wallet_id),
                     "amount": f"{money_amount_string(amount_minor=payment.amount_minor, currency=payment.currency)} {payment.currency}"},
        ))
    return items


async def _operation_context(db: AsyncSession, operation: ExternalOperation) -> dict[str, str]:
    context = {"failure": operation.failure_code or "-"}
    if operation.action == "wallet_transfer":
        transfer = await db.get(WalletTransfer, _transfer_id(operation.operation_key))
        if transfer:
            sender = await db.get(Wallet, transfer.from_wallet_id)
            receiver = await db.get(Wallet, transfer.to_wallet_id)
            context.update({
                "amount": f"{money_amount_string(amount_minor=transfer.amount_minor, currency=transfer.currency)} {transfer.currency}",
                "from": sender.external_wallet_address if sender else str(transfer.from_wallet_id),
                "to": receiver.external_wallet_address if receiver else str(transfer.to_wallet_id),
            })
    elif operation.action.startswith("wallet_"):
        wallet = await db.get(Wallet, operation.resource_id)
        if wallet:
            context.update({"wallet": str(wallet.id), "username": wallet.external_wallet_username or "-",
                            "address": wallet.external_wallet_address or "-"})
    elif operation.action == "patient_create":
        patient = await db.get(Patients, operation.resource_id)
        if patient:
            context["clinic"] = str(patient.clinic_id)
    else:  # appointment_create, commslog, popup: key is "<action>:<clinic id>:<event hash>"
        parts = operation.operation_key.split(":")
        if len(parts) >= 2:
            context["clinic"] = parts[1]
    return context


# --------------------------------------------------------------- resolve

async def resolve(db: AsyncSession, item_id: UUID, *, happened: bool, resolved_by: str, note: str = "",
                  fields: dict | None = None, force: bool = False) -> str:
    """Record what the operator found at the provider. Returns a one-line summary."""
    resolved_by = (resolved_by or "").strip()
    if not resolved_by:
        raise InvalidRequest("Say who checked the provider (resolved_by)")

    operation = await db.scalar(select(ExternalOperation).where(ExternalOperation.id == item_id).with_for_update().limit(1))
    if operation:
        return await _resolve_operation(db, operation, happened=happened, resolved_by=resolved_by,
                                        note=note, fields=fields or {}, force=force)
    payment = await db.scalar(select(PaymentTransaction).where(PaymentTransaction.id == item_id).with_for_update().limit(1))
    if payment:
        return await _resolve_funding_start(db, payment, happened=happened, resolved_by=resolved_by,
                                            note=note, fields=fields or {}, force=force)
    raise NotFound("No stuck operation or funding start has that id")


def _check_not_running(created_at: datetime, *, running: bool, force: bool) -> None:
    if running and _age(created_at, _now()) < STILL_RUNNING and not force:
        raise Conflict("This call started less than 10 minutes ago and may still be running; wait, or use force")


def _success_result(action: str, fields: dict, resolved_by: str) -> dict:
    required = SUCCESS_FIELDS.get(action)
    if required is None:
        raise InvalidRequest(f"Unknown action '{action}'")
    missing = [name for name in required if not str(fields.get(name, "")).strip()]
    if missing:
        raise InvalidRequest(f"Provide {', '.join(missing)} from the provider")
    try:
        values = {name: int(fields[name]) if name in INTEGER_FIELDS else str(fields[name]).strip() for name in required}
    except ValueError as exc:
        raise InvalidRequest("PatNum and AptNum must be whole numbers") from exc

    # Shape each result exactly as the workflow stores a real provider reply.
    if action == "wallet_transfer":
        return {"result": True, "data": [{"TX_ID": values["TX_ID"]}], "confirmed_by": resolved_by}
    if action == "wallet_password_rotation":
        return {"result": True}
    if action in ("commslog", "popup"):
        return {"created": True}
    return values


async def _resolve_operation(db: AsyncSession, operation: ExternalOperation, *, happened: bool, resolved_by: str,
                             note: str, fields: dict, force: bool) -> str:
    if operation.state not in UNKNOWN_STATES:
        raise Conflict(f"This operation is already '{operation.state}'; nothing to resolve")
    _check_not_running(operation.created_at, running=operation.state == "in_flight", force=force)

    action, key = operation.action, operation.operation_key
    transfer = None
    if action == "wallet_transfer":
        transfer = await db.scalar(select(WalletTransfer).where(WalletTransfer.id == _transfer_id(key)).with_for_update().limit(1))
        if transfer is None or transfer.status != WalletTransferStatus.PENDING:
            raise Conflict("The transfer behind this operation is not pending; check it before resolving")

    if happened:
        result = _success_result(action, fields, resolved_by)
        operation.result_encrypted = encrypt_json_secret(result)
        operation.state = "succeeded"
        operation.failure_code = None
        if action == "wallet_password_rotation":
            await _apply_new_password(db, operation)
    else:
        operation.state = "failed"
        operation.failure_code = "operator_confirmed_not_done"
        # Free the key so the next attempt calls the provider afresh; this row stays as evidence.
        operation.operation_key = f"{key}#retired-{uuid4().hex[:12]}"
        if transfer is not None:
            await _fail_transfer(db, transfer, reason=f"Confirmed not sent at ToroForge (checked by {resolved_by})")

    record_audit(db, entity_type="external_operation", entity_id=operation.id,
                 action="operator_resolved_happened" if happened else "operator_resolved_not_done",
                 details={"operation": action, "resolved_by": resolved_by, "note": note})
    await db.commit()

    if happened and action == "wallet_password_rotation":
        return "Password change recorded as done; the wallet now uses the new password."
    if happened and transfer is not None:
        # Post the balances now from the confirmed result; nothing is sent to ToroForge again.
        await ToroForgeWalletTransferService(db, transfer_client=None).post_confirmed_transfer(
            transfer.id, result, reused=False)
        return "Transfer recorded as completed; both wallet balances are updated."
    if happened:
        return "Recorded as done. The next retry of this action reuses the result instead of calling the provider."
    if transfer is not None:
        return "Transfer recorded as not sent; balances are unchanged and the DSO can transfer again."
    return "Recorded as not done. The next attempt calls the provider again."


async def _apply_new_password(db: AsyncSession, operation: ExternalOperation) -> None:
    # ToroForge already uses the new password; the wallet must too, or its next transfer fails.
    new_password = (decode_json_secret(operation.secrets_encrypted) or {}).get("new_password")
    wallet = await db.scalar(select(Wallet).where(Wallet.id == operation.resource_id).with_for_update().limit(1))
    if not new_password or wallet is None:
        raise Conflict("The new password or its wallet is missing; check it before resolving")
    wallet.external_wallet_password_encrypted = encrypt_secret(new_password)
    operation.state = "applied"


async def _fail_transfer(db: AsyncSession, transfer: WalletTransfer, *, reason: str) -> None:
    transfer.status = WalletTransferStatus.FAILED
    transfer.failure_reason = reason
    ledgers = (await db.scalars(select(WalletLedgerEntry).where(
        WalletLedgerEntry.reference_type == "wallet_transfer",
        WalletLedgerEntry.reference_id == str(transfer.id),
    ).with_for_update())).all()
    for ledger in ledgers:
        if ledger.status != LedgerStatus.POSTED:
            ledger.status = LedgerStatus.FAILED
            ledger.failure_reason = reason
            ledger.details = {**(ledger.details or {}), "stage": "operator_confirmed_not_sent", "failure_reason": reason}
    sender = await db.get(Wallet, transfer.from_wallet_id)
    receiver = await db.get(Wallet, transfer.to_wallet_id)
    await invalidate_billing_scope(clinic_id=receiver.clinic_id if receiver else None,
                                   dso_id=sender.dso_id if sender else None)


async def _resolve_funding_start(db: AsyncSession, payment: PaymentTransaction, *, happened: bool, resolved_by: str,
                                 note: str, fields: dict, force: bool) -> str:
    if payment.status != PaymentTransactionStatus.PENDING or payment.external_payment_id is not None:
        raise Conflict("This payment is not an unfinished funding start; nothing to resolve")
    _check_not_running(payment.created_at, running=True, force=force)

    record_audit(db, clinic_id=payment.clinic_id, entity_type="payment", entity_id=payment.id,
                 action="operator_resolved_happened" if happened else "operator_resolved_not_done",
                 details={"operation": "funding_start", "resolved_by": resolved_by, "note": note})

    if happened:
        # Link ToroForge's payment so the normal deposit verification can credit it.
        # Nothing is credited here: verify-deposit still checks the payment with ToroForge.
        txid = _success_result("funding_start", fields, resolved_by)["TX_ID"]
        ledger = await db.scalar(select(WalletLedgerEntry).where(
            WalletLedgerEntry.reference_type == "payment_transaction",
            WalletLedgerEntry.reference_id == str(payment.id),
            WalletLedgerEntry.entry_type == LedgerEntryType.TOP_UP,
            WalletLedgerEntry.direction == LedgerDirection.CREDIT,
        ).with_for_update().limit(1))
        payment.external_payment_id = txid
        payment.details = {**(payment.details or {}), "stage": "operator_linked_provider_payment"}
        if ledger:
            ledger.external_transaction_id = txid
        await db.commit()
        return f"Payment linked to ToroForge transaction {txid}; verify the deposit to credit the wallet."

    await ToroForgeFundingService(db, payment_client=None).record_verified_funding_failure(
        payment_transaction_id=payment.id,
        reason=f"Confirmed no payment was started at ToroForge (checked by {resolved_by})",
    )
    return "Funding start closed as failed; the wallet can start a new top-up."


# ------------------------------------------------------------ automatic

AUTOMATIC_CHECK = "automatic check"


@dataclass
class AutoResult:
    item: StuckItem
    finding: Finding
    outcome: str  # "resolved", "would resolve" (dry run), "waiting", "needs a person" or "error"
    summary: str


async def auto_resolve(db: AsyncSession, checks, *, dry_run: bool = False) -> list[AutoResult]:
    """Check every stuck action with its provider and resolve the clear ones.

    Unclear answers, provider errors and calls that may still be running are left
    untouched for the next run or for a person. dry_run reports without changing anything.
    """
    results = []
    for item in await list_unknown_outcomes(db):
        if item.age < STILL_RUNNING:
            finding = Finding(WAITING, "Started less than 10 minutes ago; it may still be running")
            results.append(AutoResult(item, finding, "waiting", finding.evidence))
            continue
        try:
            finding = await checks.check(item)
        except Exception as exc:  # one provider failing must not stop the other checks
            logger.warning("Automatic check failed for %s %s: %s", item.action, item.id, type(exc).__name__)
            await db.rollback()
            finding = Finding(UNCLEAR, f"Could not ask the provider ({type(exc).__name__})")
            results.append(AutoResult(item, finding, "error", f"{finding.evidence}; checked again on the next run"))
            continue
        if finding.outcome in (WAITING, UNCLEAR):
            label = "waiting" if finding.outcome == WAITING else "needs a person"
            results.append(AutoResult(item, finding, label, finding.evidence))
            continue
        if dry_run:
            results.append(AutoResult(item, finding, "would resolve", finding.evidence))
            continue
        try:
            summary = await resolve(db, item.id, happened=finding.outcome == HAPPENED,
                                    resolved_by=AUTOMATIC_CHECK, note=finding.evidence, fields=finding.fields)
            results.append(AutoResult(item, finding, "resolved", summary))
        except ServiceError as exc:
            await db.rollback()
            results.append(AutoResult(item, finding, "needs a person", f"Not resolved: {exc.detail}"))
        except Exception as exc:
            logger.exception("Automatic resolution failed for %s %s", item.action, item.id)
            await db.rollback()
            results.append(AutoResult(item, finding, "error", f"Not resolved: {type(exc).__name__}"))
    return results
