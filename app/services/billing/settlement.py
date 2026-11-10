"""Fail closed until the merchant has verified its provider settlement schema.

The public provider manual documents lookup operations, not settlement response
fields. Query success and wallet balance alone are not proof of this deposit.
"""
from app.config import settings


def is_settled(record: dict) -> bool:
    field = settings.toroforge_settlement_field
    allowed = settings.toroforge_settlement_success_values
    if not field or not allowed:
        return False
    value = record
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return isinstance(value, (str, int, bool)) and str(value).strip().casefold() in {
        str(item).strip().casefold() for item in allowed
    }
