# billing/toroforge/money.py

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from app.infrastructure.providers.toroforge.exceptions import ToroForgeValidationError

MAX_MINOR = 2**63 - 1


def checked_decimal(amount, *, allow_zero=False) -> Decimal:
    try:
        value = amount if isinstance(amount, Decimal) else Decimal(str(amount).strip())
        if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
            raise ValueError
        if value > Decimal(MAX_MINOR) / 100:
            raise ValueError
        return value
    except (InvalidOperation, AttributeError, ValueError, OverflowError) as exc:
        raise ToroForgeValidationError("Amount must be finite, positive and within supported limits") from exc


def currency_decimals(currency: str) -> int:
    decimals_map = {
        "USD": 2,
        "EUR": 2,
        "GBP": 2,
        "NGN": 2,
    }

    normalized_currency = currency.strip().upper()
    decimals = decimals_map.get(normalized_currency)

    if decimals is None:
        raise ToroForgeValidationError(f"Unsupported currency: {currency}")

    return decimals


def coerce_amount_decimal(amount: str | Decimal | int | float) -> Decimal:
    return checked_decimal(amount)


def normalize_amount(*, amount: Decimal, currency: str) -> Decimal:
    decimals = currency_decimals(currency)
    quantizer = Decimal("1").scaleb(-decimals)

    amount = checked_decimal(amount, allow_zero=True)
    try:
        normalized = amount.quantize(quantizer, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ToroForgeValidationError("Invalid amount precision") from exc
    if normalized != amount:
        raise ToroForgeValidationError("Amount has unsupported fractional minor units")
    return normalized


def to_amount_minor(*, amount: Decimal, currency: str) -> int:
    decimals = currency_decimals(currency)
    normalized_amount = normalize_amount(amount=amount, currency=currency)
    multiplier = Decimal(10) ** decimals

    amount_minor = int(normalized_amount * multiplier)

    if amount_minor <= 0:
        raise ToroForgeValidationError("Amount must be greater than zero")

    return amount_minor


def to_provider_amount_string(*, amount: Decimal, currency: str) -> str:
    decimals = currency_decimals(currency)
    normalized_amount = normalize_amount(amount=amount, currency=currency)

    return f"{normalized_amount:.{decimals}f}"


def extract_address_balance_minor(
    *,
    balance_response: dict[str, Any],
    currency: str,
) -> int:
    normalized_currency = currency.strip().upper()

    balance_keys = {
        "NGN": "bal_naira",
        "USD": "bal_dollar",
    }

    balance_key = balance_keys.get(normalized_currency)

    if not balance_key:
        raise ToroForgeValidationError(
            f"Address balance check is not supported for {normalized_currency}"
        )

    raw_balance = balance_response.get(balance_key)

    if raw_balance is None:
        raise ToroForgeValidationError(
            f"ToroForge balance response is missing {balance_key}"
        )

    return balance_amount_to_minor(
        amount=raw_balance,
        currency=normalized_currency,
    )


def balance_amount_to_minor(
    *,
    amount: Any,
    currency: str,
) -> int:
    decimal_amount = checked_decimal(amount, allow_zero=True)

    decimals = currency_decimals(currency)
    quantizer = Decimal("1").scaleb(-decimals)

    normalized_amount = decimal_amount.quantize(
        quantizer,
        rounding=ROUND_HALF_UP,
    )

    return int(normalized_amount * (Decimal(10) ** decimals))
