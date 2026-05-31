from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from caches.toroforge_billing_cache import (
    CLINIC__BILLING_TTL_SECONDS,
    DSO_BILLING_TTL_SECONDS,
    cache_get_json,
    cache_set_json,
    clinic_billing_cache_key,
    dso_billing_cache_key,
)
from core.models import (
    BillingSubscription,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentTransaction,
    PaymentTransactionStatus,
    RegisteredClinics,
    ScopeType,
    SubscriptionStatus,
    Wallet,
    WalletLedgerEntry,
    WalletType,
)
from core.schemas import (
    toroforge_billing_subscription_out,
    toroforge_clinic_billing_out,
    toroforge_dso_billing_out,
    toroforge_wallet_ledger_row_out,
    toroforge_wallet_read_item_out,
)


def month_window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def wallet_label(*, wallet: Wallet, clinic_name: str | None = None) -> str:
    if wallet.wallet_type == WalletType.DSO_TREASURY:
        return "DSO Treasury"
    return clinic_name or wallet.external_wallet_username or "Clinic Wallet"


def event_label(entry_type: LedgerEntryType) -> str:
    labels = {
        LedgerEntryType.TOP_UP: "ToroForge wallet top-up",
        LedgerEntryType.TRANSFER_OUT: "Funded clinic wallet",
        LedgerEntryType.TRANSFER_IN: "Treasury allocation received",
        LedgerEntryType.SUBSCRIPTION_CHARGE: "FumiSync subscription",
        LedgerEntryType.USAGE_CHARGE: "Premium usage batch",
        LedgerEntryType.REFUND: "Wallet refund",
        LedgerEntryType.ADJUSTMENT: "Wallet adjustment",
    }
    return labels.get(entry_type, entry_type.value.replace("_", " ").title())


def details_value(entry: WalletLedgerEntry, *keys: str) -> str | None:
    details = entry.details or {}
    for key in keys:
        value = details.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def reference_kind(entry: WalletLedgerEntry) -> str | None:
    explicit = details_value(entry, "reference_label", "reference_kind", "display_reference_type")
    if explicit:
        return explicit

    labels = {
        LedgerEntryType.TOP_UP: "Top-up",
        LedgerEntryType.TRANSFER_OUT: "Transfer",
        LedgerEntryType.TRANSFER_IN: "Transfer",
        LedgerEntryType.SUBSCRIPTION_CHARGE: "Subscription",
        LedgerEntryType.USAGE_CHARGE: "Usage",
        LedgerEntryType.REFUND: "Refund",
        LedgerEntryType.ADJUSTMENT: "Adjustment",
    }
    return labels.get(entry.entry_type)


def reference_code(entry: WalletLedgerEntry) -> str | None:
    return (
         entry.external_transaction_id
    )


def ledger_event_subtitle(entry: WalletLedgerEntry) -> str | None:
    kind = reference_kind(entry)
    code = reference_code(entry)

    if kind and code:
        return f"{kind} - {code}"
    if code:
        return code
    return kind


def ledger_event_label(
    *,
    entry: WalletLedgerEntry,
    wallet: Wallet,
    clinic_name: str | None,
    counterparty_wallet: Wallet | None,
    counterparty_clinic_name: str | None,
) -> str:
    description = details_value(entry, "description", "display_label", "label")
    if description:
        return description

    if entry.entry_type == LedgerEntryType.SUBSCRIPTION_CHARGE:
        plan_name = details_value(entry, "plan_name", "plan", "billing_plan", "subscription_plan")
        if plan_name:
            return f"{plan_name} subscription"
        return "FumiSync subscription"

    if entry.entry_type == LedgerEntryType.USAGE_CHARGE:
        feature_name = details_value(entry, "feature_name", "feature_code", "usage_name")
        if feature_name:
            return f"{feature_name} usage"
        return "Premium usage batch"

    if entry.entry_type == LedgerEntryType.TRANSFER_OUT:
        funded_name = (
            counterparty_clinic_name
            or (
                wallet_label(wallet=counterparty_wallet)
                if counterparty_wallet is not None
                else None
            )
        )
        return f"Funded {funded_name}" if funded_name else "Funded clinic wallet"

    if entry.entry_type == LedgerEntryType.TRANSFER_IN:
        if wallet.wallet_type == WalletType.CLINIC:
            return "Treasury allocation received"
        source_name = (
            counterparty_clinic_name
            or (
                wallet_label(wallet=counterparty_wallet)
                if counterparty_wallet is not None
                else None
            )
        )
        return f"Received from {source_name}" if source_name else "Treasury allocation received"

    if entry.entry_type == LedgerEntryType.TOP_UP:
        return "ToroForge wallet top-up"

    return event_label(entry.entry_type)


def to_wallet_item(
    *,
    wallet: Wallet,
    clinic_name: str | None = None,
) -> toroforge_wallet_read_item_out:
    return toroforge_wallet_read_item_out(
        wallet_id=wallet.id,
        wallet_type=wallet.wallet_type.value,
        wallet_label=wallet_label(wallet=wallet, clinic_name=clinic_name),
        clinic_id=wallet.clinic_id,
        clinic_name=clinic_name,
        dso_id=wallet.dso_id,
        status=wallet.status.value,
        currency=wallet.currency,
        available_balance_minor=wallet.cached_balance_minor,
        external_wallet_username=wallet.external_wallet_username,
        external_wallet_address=wallet.external_wallet_address,
        auto_debit_enabled=wallet.auto_debit_enabled,
        last_balance_sync_at=wallet.last_balance_sync_at,
    )


def to_ledger_row(
    *,
    entry: WalletLedgerEntry,
    wallet: Wallet,
    clinic_name: str | None = None,
    counterparty_wallet: Wallet | None = None,
    counterparty_clinic_name: str | None = None,
) -> toroforge_wallet_ledger_row_out:
    counterparty_wallet_label = (
        wallet_label(wallet=counterparty_wallet, clinic_name=counterparty_clinic_name)
        if counterparty_wallet is not None
        else None
    )

    return toroforge_wallet_ledger_row_out(
        ledger_entry_id=entry.id,
        wallet_id=wallet.id,
        wallet_label=wallet_label(wallet=wallet, clinic_name=clinic_name),
        counterparty_wallet_id=counterparty_wallet.id if counterparty_wallet else None,
        counterparty_wallet_label=counterparty_wallet_label,
        counterparty_clinic_id=counterparty_wallet.clinic_id if counterparty_wallet else None,
        counterparty_clinic_name=counterparty_clinic_name,
        event_type=entry.entry_type.value,
        event_label=ledger_event_label(
            entry=entry,
            wallet=wallet,
            clinic_name=clinic_name,
            counterparty_wallet=counterparty_wallet,
            counterparty_clinic_name=counterparty_clinic_name,
        ),
        event_subtitle=ledger_event_subtitle(entry),
        direction=entry.direction.value,
        status=entry.status.value,
        amount_minor=entry.amount_minor,
        balance_after_minor=entry.balance_after_minor,
        currency=entry.currency,
        created_at=entry.created_at,
        posted_at=entry.posted_at,
        reference_type=entry.reference_type,
        reference_id=entry.reference_id,
    )


def to_subscription_item(subscription: BillingSubscription) -> toroforge_billing_subscription_out:
    return toroforge_billing_subscription_out(
        subscription_id=subscription.id,
        plan_code=subscription.plan_code.value,
        status=subscription.status.value,
        next_billing_at=subscription.next_billing_at,
        amount_minor=subscription.base_price_minor,
        currency=subscription.currency,
        payment_provider=subscription.payment_provider.value,
    )


def active_subscription_statuses() -> list[SubscriptionStatus]:
    return [
        SubscriptionStatus.TRAILING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
    ]


def build_recent_ledger_rows(
    *,
    db: Session,
    wallet_filter,
    limit: int = 10,
) -> list[toroforge_wallet_ledger_row_out]:
    CounterpartyWallet = aliased(Wallet)
    CounterpartyClinic = aliased(RegisteredClinics)

    ledger_rows = (
        db.query(
            WalletLedgerEntry,
            Wallet,
            RegisteredClinics,
            CounterpartyWallet,
            CounterpartyClinic,
        )
        .join(Wallet, Wallet.id == WalletLedgerEntry.wallet_id)
        .outerjoin(RegisteredClinics, RegisteredClinics.id == Wallet.clinic_id)
        .outerjoin(
            CounterpartyWallet,
            CounterpartyWallet.id == WalletLedgerEntry.counterparty_wallet_id,
        )
        .outerjoin(
            CounterpartyClinic,
            CounterpartyClinic.id == CounterpartyWallet.clinic_id,
        )
        .filter(wallet_filter)
        .order_by(
            func.coalesce(WalletLedgerEntry.posted_at, WalletLedgerEntry.created_at).desc(),
            WalletLedgerEntry.created_at.desc(),
        )
        .limit(limit)
        .all()
    )

    return [
        to_ledger_row(
            entry=entry,
            wallet=wallet,
            clinic_name=clinic.clinic_name if clinic else None,
            counterparty_wallet=counterparty_wallet,
            counterparty_clinic_name=(
                counterparty_clinic.clinic_name if counterparty_clinic else None
            ),
        )
        for entry, wallet, clinic, counterparty_wallet, counterparty_clinic in ledger_rows
    ]


def build_dso_billing_command_center_cached(
    db: Session,
    *,
    dso_id: UUID,
) -> toroforge_dso_billing_out:
    cache_key = dso_billing_cache_key(dso_id=dso_id)
    cached = cache_get_json(cache_key)
    if cached:
        return toroforge_dso_billing_out(**cached)

    month_start, month_end = month_window()

    treasury_wallet = (
        db.query(Wallet)
        .filter(
            Wallet.dso_id == dso_id,
            Wallet.wallet_type == WalletType.DSO_TREASURY,
        )
        .first()
    )

    if not treasury_wallet:
        response = toroforge_dso_billing_out(
            has_wallet=False,
            next_action="create_wallet",
            message="No DSO treasury wallet found. Create a wallet first.",
            generated_at=datetime.now(timezone.utc),
            dso_id=dso_id,
            treasury_wallet=None,
            billing_health_status="attention",
            billing_health_reason="No DSO treasury wallet found",
        )
        cache_set_json(cache_key, response.model_dump(mode="json"), DSO_BILLING_TTL_SECONDS)
        return response

    clinic_rows = (
        db.query(Wallet, RegisteredClinics)
        .outerjoin(RegisteredClinics, RegisteredClinics.id == Wallet.clinic_id)
        .filter(
            Wallet.dso_id == dso_id,
            Wallet.wallet_type == WalletType.CLINIC,
        )
        .order_by(RegisteredClinics.clinic_name.asc())
        .all()
    )

    clinic_wallets = [
        to_wallet_item(wallet=wallet, clinic_name=clinic.clinic_name if clinic else None)
        for wallet, clinic in clinic_rows
    ]

    wallet_inflow_this_month_minor = int(
        db.query(func.coalesce(func.sum(WalletLedgerEntry.amount_minor), 0))
        .filter(
            WalletLedgerEntry.wallet_id == treasury_wallet.id,
            WalletLedgerEntry.entry_type == LedgerEntryType.TOP_UP,
            WalletLedgerEntry.direction == LedgerDirection.CREDIT,
            WalletLedgerEntry.status == LedgerStatus.POSTED,
            WalletLedgerEntry.posted_at >= month_start,
            WalletLedgerEntry.posted_at < month_end,
        )
        .scalar()
        or 0
    )

    premium_charges_this_month_minor = int(
        db.query(func.coalesce(func.sum(WalletLedgerEntry.amount_minor), 0))
        .filter(
            WalletLedgerEntry.wallet_id == treasury_wallet.id,
            WalletLedgerEntry.entry_type.in_(
                [LedgerEntryType.SUBSCRIPTION_CHARGE, LedgerEntryType.USAGE_CHARGE]
            ),
            WalletLedgerEntry.direction == LedgerDirection.DEBIT,
            WalletLedgerEntry.status == LedgerStatus.POSTED,
            WalletLedgerEntry.posted_at >= month_start,
            WalletLedgerEntry.posted_at < month_end,
        )
        .scalar()
        or 0
    )

    failed_payment_count = int(
        db.query(func.count(PaymentTransaction.id))
        .filter(
            PaymentTransaction.dso_id == dso_id,
            PaymentTransaction.status == PaymentTransactionStatus.FAILED,
            PaymentTransaction.created_at >= month_start,
            PaymentTransaction.created_at < month_end,
        )
        .scalar()
        or 0
    )

    recent_ledger = build_recent_ledger_rows(
        db=db,
        wallet_filter=Wallet.dso_id == dso_id,
    )

    subscription = (
        db.query(BillingSubscription)
        .filter(
            BillingSubscription.scope_type == ScopeType.DSO,
            BillingSubscription.dso_id == dso_id,
            BillingSubscription.status.in_(active_subscription_statuses()),
        )
        .order_by(BillingSubscription.created_at.desc())
        .first()
    )

    response = toroforge_dso_billing_out(
        has_wallet=True,
        next_action=None,
        message=None,
        generated_at=datetime.now(timezone.utc),
        dso_id=dso_id,
        treasury_wallet=to_wallet_item(wallet=treasury_wallet),
        clinic_wallet_count=len(clinic_wallets),
        clinic_wallets=clinic_wallets,
        wallet_inflow_this_month_minor=wallet_inflow_this_month_minor,
        premium_charges_this_month_minor=premium_charges_this_month_minor,
        failed_payment_count=failed_payment_count,
        billing_health_status="good" if failed_payment_count == 0 else "attention",
        billing_health_reason=(
            None
            if failed_payment_count == 0
            else "There are failed payment attempts this month"
        ),
        recent_ledger=recent_ledger,
        active_subscription=to_subscription_item(subscription) if subscription else None,
    )

    cache_set_json(cache_key, response.model_dump(mode="json"), DSO_BILLING_TTL_SECONDS)
    return response


def build_clinic_billing_command_center_cached(
    db: Session,
    *,
    clinic_id: UUID,
) -> toroforge_clinic_billing_out:
    cache_key = clinic_billing_cache_key(clinic_id=clinic_id)
    cached = cache_get_json(cache_key)
    if cached:
        return toroforge_clinic_billing_out(**cached)

    month_start, month_end = month_window()

    clinic = db.query(RegisteredClinics).filter(RegisteredClinics.id == clinic_id).first()
    if not clinic:
        raise ValueError("Clinic not found")

    clinic_wallet = (
        db.query(Wallet)
        .filter(
            Wallet.clinic_id == clinic_id,
            Wallet.wallet_type == WalletType.CLINIC,
        )
        .first()
    )

    if not clinic_wallet:
        response = toroforge_clinic_billing_out(
            has_wallet=False,
            next_action="create_wallet",
            message="No clinic wallet found. Create a wallet first.",
            generated_at=datetime.now(timezone.utc),
            clinic_id=clinic_id,
            dso_id=clinic.dso_id,
            clinic_wallet=None,
            parent_wallet_label="DSO Wallet" if clinic.dso_id else None,
            billing_health_status="attention",
            billing_health_reason="No clinic wallet found",
        )
        cache_set_json(cache_key, response.model_dump(mode="json"), CLINIC__BILLING_TTL_SECONDS)
        return response

    wallet_inflow_this_month_minor = int(
        db.query(func.coalesce(func.sum(WalletLedgerEntry.amount_minor), 0))
        .filter(
            WalletLedgerEntry.wallet_id == clinic_wallet.id,
            WalletLedgerEntry.entry_type.in_(
                [LedgerEntryType.TOP_UP, LedgerEntryType.TRANSFER_IN]
            ),
            WalletLedgerEntry.direction == LedgerDirection.CREDIT,
            WalletLedgerEntry.status == LedgerStatus.POSTED,
            WalletLedgerEntry.posted_at >= month_start,
            WalletLedgerEntry.posted_at < month_end,
        )
        .scalar()
        or 0
    )

    premium_charges_this_month_minor = int(
        db.query(func.coalesce(func.sum(WalletLedgerEntry.amount_minor), 0))
        .filter(
            WalletLedgerEntry.wallet_id == clinic_wallet.id,
            WalletLedgerEntry.entry_type.in_(
                [LedgerEntryType.SUBSCRIPTION_CHARGE, LedgerEntryType.USAGE_CHARGE]
            ),
            WalletLedgerEntry.direction == LedgerDirection.DEBIT,
            WalletLedgerEntry.status == LedgerStatus.POSTED,
            WalletLedgerEntry.posted_at >= month_start,
            WalletLedgerEntry.posted_at < month_end,
        )
        .scalar()
        or 0
    )

    failed_payment_count = int(
        db.query(func.count(PaymentTransaction.id))
        .filter(
            PaymentTransaction.clinic_id == clinic_id,
            PaymentTransaction.status == PaymentTransactionStatus.FAILED,
            PaymentTransaction.created_at >= month_start,
            PaymentTransaction.created_at < month_end,
        )
        .scalar()
        or 0
    )

    recent_ledger = build_recent_ledger_rows(
        db=db,
        wallet_filter=Wallet.id == clinic_wallet.id,
    )

    subscription = (
        db.query(BillingSubscription)
        .filter(
            BillingSubscription.scope_type == ScopeType.CLINIC,
            BillingSubscription.clinic_id == clinic_id,
            BillingSubscription.status.in_(active_subscription_statuses()),
        )
        .order_by(BillingSubscription.created_at.desc())
        .first()
    )

    response = toroforge_clinic_billing_out(
        has_wallet=True,
        next_action=None,
        message=None,
        generated_at=datetime.now(timezone.utc),
        clinic_id=clinic_id,
        dso_id=clinic.dso_id,
        clinic_wallet=to_wallet_item(wallet=clinic_wallet, clinic_name=clinic.clinic_name),
        parent_wallet_label="DSO Wallet" if clinic.dso_id else None,
        wallet_inflow_this_month_minor=wallet_inflow_this_month_minor,
        premium_charges_this_month_minor=premium_charges_this_month_minor,
        failed_payment_count=failed_payment_count,
        billing_health_status="good" if failed_payment_count == 0 else "attention",
        billing_health_reason=(
            None
            if failed_payment_count == 0
            else "There are failed payment attempts this month"
        ),
        recent_ledger=recent_ledger,
        active_subscription=to_subscription_item(subscription) if subscription else None,
    )

    cache_set_json(cache_key, response.model_dump(mode="json"), CLINIC__BILLING_TTL_SECONDS)
    return response
