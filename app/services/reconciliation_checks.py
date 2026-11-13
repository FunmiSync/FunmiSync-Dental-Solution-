"""Ask ToroForge and OpenDental, read-only, whether a stuck action really happened.

Each check answers with a Finding:
- HAPPENED  exactly one provider record matches, so its id is reused;
- NOT_DONE  the provider shows nothing after long enough that it would have shown;
- WAITING   nothing shows yet, but it is too early to call it not done;
- UNCLEAR   anything else (several matches, a near match, a provider error). A person decides.

The checks never write to a provider. reconciliation_service applies the answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError
from app.models.entities import (
    ExternalOperation, Patients, PaymentTransaction, RegisteredClinics, Wallet, WalletTransfer,
)
from app.security.encryption import decode_json_secret
from app.services.billing.money import coerce_amount_decimal, to_amount_minor
from app.services.billing_service import money_amount_string
from app.services.external_operations import stored_request

HAPPENED, NOT_DONE, WAITING, UNCLEAR = "happened", "not_done", "waiting", "unclear"

# Silence from a provider only means "not done" once this much time has passed:
# Toronet and OpenDental show a completed action within minutes.
NOT_DONE_AFTER = timedelta(hours=1)
# OpenDental answers or gives up within seconds (our client stops waiting after 15), so a
# booking or patient that is still missing after this long was not created. A shorter wait
# matters here: the clinic's later changes to that appointment wait until it is settled.
OPENDENTAL_NOT_DONE_AFTER = timedelta(minutes=10)
# Toronet's clock and ours can differ slightly; accept records this much older than our row.
CLOCK_SKEW = timedelta(minutes=2)
# Newest transactions read from Toronet. If all of them are newer than the transfer,
# older history was cut off and "nothing found" proves nothing.
TRANSFER_HISTORY_SIZE = 100


@dataclass(frozen=True)
class Finding:
    outcome: str
    evidence: str
    fields: dict[str, str] = field(default_factory=dict)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_provider_time(value: Any) -> datetime | None:
    try:
        return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def same_address(left: Any, right: str) -> bool:
    return str(left or "").strip().lower() == right.strip().lower()


def amount_minor_or_none(value: Any, currency: str) -> int | None:
    """Exact minor units, or None when the amount is missing or has fractional cents."""
    if value is None:
        return None
    try:
        return to_amount_minor(amount=coerce_amount_decimal(value), currency=currency)
    except ToroForgeValidationError:
        return None


def money(amount_minor: int, currency: str) -> str:
    return f"{money_amount_string(amount_minor=amount_minor, currency=currency)} {currency}"


def not_done_or_wait(age: timedelta, evidence: str, *, after: timedelta = NOT_DONE_AFTER) -> Finding:
    if age < after:
        wait = "an hour" if after == timedelta(hours=1) else f"{int(after.total_seconds() // 60)} minutes"
        return Finding(WAITING, f"{evidence} so far; it is closed as not done once it is {wait} old")
    return Finding(NOT_DONE, evidence)


def appointment_to_look_for(request: dict | None) -> tuple[str, str] | None:
    """(chair, day) to read from OpenDental for a create request; None if its details were not kept."""
    if not request or not request.get("AptDateTime") or not request.get("Op") or not request.get("PatNum"):
        return None
    return str(request["Op"]), str(request["AptDateTime"])[:10]


def created_appointment_finding(appointments: list[dict], request: dict, age: timedelta) -> Finding:
    """Did a create that timed out land in OpenDental? Used by the automatic check and by
    the booking itself. appointments: what OpenDental lists for that chair and day."""
    when = str(request["AptDateTime"])[:19]
    matches = [appointment for appointment in appointments or []
               if str(appointment.get("PatNum")) == str(request["PatNum"])
               and str(appointment.get("AptDateTime") or "")[:19] == when and appointment.get("AptNum")]
    if len(matches) == 1:
        return Finding(HAPPENED, f"OpenDental has the appointment at {when}: AptNum {matches[0]['AptNum']}",
                       {"AptNum": str(matches[0]["AptNum"])})
    if matches:
        return Finding(UNCLEAR, f"OpenDental has {len(matches)} appointments for this patient at {when}")
    return not_done_or_wait(age, f"OpenDental has no appointment for this patient at {when}",
                            after=OPENDENTAL_NOT_DONE_AFTER)


def history_reaches_back(records: list[dict], since: datetime) -> bool:
    """True when Toronet returned everything since `since`, not just the newest page."""
    if len(records) < TRANSFER_HISTORY_SIZE:
        return True
    times = [parse_provider_time(record.get("EV_Time")) for record in records]
    return any(when is not None and when < since for when in times)


class ProviderChecks:
    """Read-only provider questions for each kind of stuck action."""

    def __init__(self, db: AsyncSession, *, transfers, payments, tns, keystore,
                 opendental_for: Callable[[RegisteredClinics], Any]) -> None:
        self.db = db
        self.transfers = transfers          # ToroForgeWalletTransferClient
        self.payments = payments            # ToroForgePaymentClient
        self.tns = tns                      # ToroForgeTNSClient
        self.keystore = keystore            # ToroForgeKeyStoreClient
        self.opendental_for = opendental_for

    async def check(self, item) -> Finding:
        if item.kind == "funding_start":
            return await self.funding_start(item, await self.db.get(PaymentTransaction, item.id))
        operation = await self.db.get(ExternalOperation, item.id)
        check = {
            "wallet_transfer": self.wallet_transfer,
            "wallet_keystore": self.wallet_keystore,
            "wallet_tns": self.wallet_tns,
            "wallet_password_rotation": self.wallet_password_rotation,
            "patient_create": self.patient_create,
            "appointment_create": self.appointment_create,
        }.get(item.action)
        if operation is None or check is None:
            return Finding(UNCLEAR, "Not checked automatically; a person must check the provider")
        return await check(item, operation)

    # ------------------------------------------------------------ ToroForge

    async def wallet_transfer(self, item, operation: ExternalOperation) -> Finding:
        transfer = await self.db.get(WalletTransfer, UUID(operation.operation_key.split("#", 1)[0].split(":", 1)[1]))
        sender = await self.db.get(Wallet, transfer.from_wallet_id) if transfer else None
        receiver = await self.db.get(Wallet, transfer.to_wallet_id) if transfer else None
        if not (sender and receiver and sender.external_wallet_address and receiver.external_wallet_address):
            return Finding(UNCLEAR, "The transfer or its wallet addresses are missing")
        sender_address, receiver_address = sender.external_wallet_address, receiver.external_wallet_address
        since = as_utc(transfer.created_at) - CLOCK_SKEW

        records = await self.transfers.get_dollar_transactions(address=sender_address, count=TRANSFER_HISTORY_SIZE)
        to_receiver = []
        for record in records:
            if not (same_address(record.get("EV_From"), sender_address) and same_address(record.get("EV_To"), receiver_address)):
                continue
            when = parse_provider_time(record.get("EV_Time"))
            if when is None:
                return Finding(UNCLEAR, "Toronet returned a transfer to this clinic without a readable time")
            if when >= since:
                to_receiver.append(record)

        hashes = [str(record.get("EV_Hash") or "").strip() for record in to_receiver]
        claimed = set((await self.db.scalars(select(WalletTransfer.external_transaction_id).where(
            WalletTransfer.external_transaction_id.in_([value for value in hashes if value])))).all())
        matches = [value for record, value in zip(to_receiver, hashes)
                   if value and value not in claimed
                   and amount_minor_or_none(record.get("EV_Value"), transfer.currency) == transfer.amount_minor]

        amount = money(transfer.amount_minor, transfer.currency)
        if len(matches) == 1:
            return Finding(HAPPENED, f"Toronet shows {amount} from {sender_address} to {receiver_address}: {matches[0]}",
                           {"TX_ID": matches[0]})
        if matches:
            return Finding(UNCLEAR, f"Toronet shows {len(matches)} transfers of {amount} to this clinic; a person must pick one")
        if to_receiver:
            return Finding(UNCLEAR, "Toronet shows a transfer to this clinic with a different amount or an already used id")
        if not history_reaches_back(records, since):
            return Finding(UNCLEAR, "The treasury has too many recent transactions to be sure; a person must check")
        return not_done_or_wait(item.age, f"No transfer from {sender_address} to {receiver_address} on Toronet "
                                          f"since {since:%Y-%m-%d %H:%M} UTC")

    async def funding_start(self, item, payment: PaymentTransaction | None) -> Finding:
        wallet = await self.db.get(Wallet, payment.wallet_id) if payment else None
        if not (wallet and wallet.external_wallet_address):
            return Finding(UNCLEAR, "The payment or its wallet address is missing")
        address, currency = wallet.external_wallet_address, payment.currency.strip().upper()
        created = as_utc(payment.created_at)
        config = self.payments.client.config
        records = await self.payments.get_payments_for_address(
            admin=config.admin, adminpwd=config.adminpwd, address=address,
            start_date=(created - timedelta(days=1)).date().isoformat(),
            end_date=(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat())

        same = [record for record in records
                if same_address(record.get("TX_Address"), address)
                and str(record.get("TX_Currency") or "").strip().upper() == currency
                and amount_minor_or_none(record.get("TX_Amount"), currency) == payment.amount_minor
                and str(record.get("TX_ID") or "").strip()]
        txids = [str(record["TX_ID"]).strip() for record in same]
        linked = set((await self.db.scalars(select(PaymentTransaction.external_payment_id).where(
            PaymentTransaction.external_payment_id.in_(txids)))).all())
        free = [txid for txid in txids if txid not in linked]

        amount = money(payment.amount_minor, currency)
        if len(free) == 1:
            return Finding(HAPPENED, f"ToroForge has a payment of {amount} for this wallet: {free[0]}", {"TX_ID": free[0]})
        if free:
            return Finding(UNCLEAR, f"ToroForge has {len(free)} unlinked payments of {amount}; a person must pick one")
        return not_done_or_wait(item.age, f"ToroForge has no unlinked payment of {amount} for this wallet")

    async def wallet_keystore(self, item, operation: ExternalOperation) -> Finding:
        wallet = await self.db.get(Wallet, operation.resource_id)
        if wallet is None or wallet.external_wallet_address:
            return Finding(UNCLEAR, "The wallet is missing or already has an address; a person must check")
        # The address never came back, so no one could have paid into a key made by this
        # call and its name was never registered. Starting again is safe.
        return not_done_or_wait(item.age, "No address came back from ToroForge")

    async def wallet_tns(self, item, operation: ExternalOperation) -> Finding:
        wallet = await self.db.get(Wallet, operation.resource_id)
        if not (wallet and wallet.external_wallet_username and wallet.external_wallet_address):
            return Finding(UNCLEAR, "The wallet's name or address is missing")
        username, address = wallet.external_wallet_username, wallet.external_wallet_address
        registered_to = await self.tns.get_address_for_name(username=username)
        if registered_to and same_address(registered_to, address):
            return Finding(HAPPENED, f"TNS name {username} points to this wallet", {"address": address, "username": username})
        if registered_to:
            return Finding(UNCLEAR, f"TNS name {username} points to a different address; a person must check")
        return not_done_or_wait(item.age, f"TNS name {username} is not registered")

    async def wallet_password_rotation(self, item, operation: ExternalOperation) -> Finding:
        wallet = await self.db.get(Wallet, operation.resource_id)
        new_password = (decode_json_secret(operation.secrets_encrypted) or {}).get("new_password")
        if not (wallet and wallet.external_wallet_address and new_password):
            return Finding(UNCLEAR, "The wallet address or the new password is missing")
        if await self.keystore.verify_key(address=wallet.external_wallet_address, password=new_password):
            return Finding(HAPPENED, "ToroForge accepts the new password")
        # The old password still working does not prove a delayed change failed.
        return Finding(UNCLEAR, "ToroForge does not accept the new password; a person must check")

    # ----------------------------------------------------------- OpenDental

    async def patient_create(self, item, operation: ExternalOperation) -> Finding:
        patient = await self.db.get(Patients, operation.resource_id)
        if patient is None:
            return Finding(UNCLEAR, "The patient row is missing")
        if patient.pat_num:
            return Finding(HAPPENED, f"A later attempt already linked OpenDental patient {patient.pat_num}",
                           {"PatNum": str(patient.pat_num)})
        request = stored_request(operation)
        if not request or not request.get("LName") or not request.get("Birthdate"):
            return Finding(UNCLEAR, "Started before request details were kept; a person must check OpenDental")
        clinic = await self.db.get(RegisteredClinics, patient.clinic_id)
        if clinic is None:
            return Finding(UNCLEAR, "The clinic is missing")
        matches = await self.opendental_for(clinic).search_patients(
            last_name=request["LName"], date_of_birth=request["Birthdate"]) or []

        def normalized(value):
            return " ".join(str(value or "").casefold().split())
        # The same identity rule the patient creation flow uses.
        candidates = [match for match in matches
                      if normalized(match.get("FName")) == normalized(request.get("FName"))
                      and normalized(match.get("LName")) == normalized(request["LName"])
                      and str(match.get("Birthdate") or "")[:10] == request["Birthdate"]]
        if len(candidates) == 1 and candidates[0].get("PatNum"):
            return Finding(HAPPENED, f"OpenDental has the patient: PatNum {candidates[0]['PatNum']}",
                           {"PatNum": str(candidates[0]["PatNum"])})
        if candidates or matches:
            return Finding(UNCLEAR, "OpenDental has similar patients; a person must pick one")
        return not_done_or_wait(item.age, "OpenDental has no such patient", after=OPENDENTAL_NOT_DONE_AFTER)

    async def appointment_create(self, item, operation: ExternalOperation) -> Finding:
        request = stored_request(operation)
        lookup = appointment_to_look_for(request)
        if lookup is None:
            return Finding(UNCLEAR, "Started before request details were kept; a person must check OpenDental")
        clinic = await self.db.get(RegisteredClinics, UUID(operation.operation_key.split(":")[1]))
        if clinic is None:
            return Finding(UNCLEAR, "The clinic is missing")
        chair, day = lookup
        appointments = await self.opendental_for(clinic).get_appointments_in_operatory(chair, day, day) or []
        return created_appointment_finding(appointments, request, item.age)
