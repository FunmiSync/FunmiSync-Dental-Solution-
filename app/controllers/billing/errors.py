"""Map ToroForge provider/service failures to HTTP errors for each billing area.

The mappings intentionally differ: for example, KYC reports every provider
outage as 502, while funding and transfers report timeouts as 503.
"""
from typing import NoReturn

from fastapi import HTTPException, status

from app.infrastructure.providers.toroforge.exceptions import (
    ToroForgeAuthError,
    ToroForgeDuplicateNameError,
    ToroForgeError,
    ToroForgeHTTPError,
    ToroForgeTimeoutError,
    ToroForgeUnavailableError,
    ToroForgeValidationError,
    ToroForgeWalletCreationError,
)


def _http(status_code: int, detail: str, exc: Exception) -> NoReturn:
    raise HTTPException(status_code=status_code, detail=detail) from exc


def raise_wallet_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeDuplicateNameError):
        _http(status.HTTP_409_CONFLICT, message or "ToroForge username is already taken", exc)

    if isinstance(exc, ToroForgeValidationError):
        _http(status.HTTP_400_BAD_REQUEST, message or "Invalid ToroForge wallet request", exc)

    if isinstance(exc, ToroForgeWalletCreationError):
        if "already exists" in lowered or "already taken" in lowered or "partial external state" in lowered:
            _http(status.HTTP_409_CONFLICT, message, exc)
        if "not found" in lowered:
            _http(status.HTTP_404_NOT_FOUND, message, exc)
        _http(status.HTTP_502_BAD_GATEWAY, "Unable to provision ToroForge wallet at this time", exc)

    _http(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error while creating ToroForge wallet", exc)


def raise_kyc_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()

    if isinstance(exc, ToroForgeValidationError):
        _http(status.HTTP_400_BAD_REQUEST, message or "Invalid ToroForge KYC request", exc)

    if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError, ToroForgeHTTPError, ToroForgeAuthError)):
        _http(status.HTTP_502_BAD_GATEWAY, "ToroForge KYC is temporarily unavailable", exc)

    _http(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error while processing ToroForge KYC", exc)


def raise_funding_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeValidationError):
        if "not found" in lowered:
            _http(status.HTTP_404_NOT_FOUND, message or "Funding resource not found", exc)
        if "already" in lowered or "posted successfully" in lowered:
            _http(status.HTTP_409_CONFLICT, message or "Funding request conflicts with existing state", exc)
        _http(status.HTTP_400_BAD_REQUEST, message or "Invalid ToroForge funding request", exc)

    if isinstance(exc, ToroForgeAuthError):
        _http(status.HTTP_502_BAD_GATEWAY, "ToroForge funding provider authentication failed", exc)

    if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError)):
        _http(status.HTTP_503_SERVICE_UNAVAILABLE, "ToroForge funding provider is temporarily unavailable", exc)

    if isinstance(exc, (ToroForgeHTTPError, ToroForgeError)):
        _http(status.HTTP_502_BAD_GATEWAY, message or "ToroForge funding provider error", exc)

    _http(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error while processing ToroForge funding", exc)


def raise_wallet_transfer_http_error(exc: Exception) -> NoReturn:
    message = str(exc).strip()
    lowered = message.lower()

    if isinstance(exc, ToroForgeValidationError):
        if "not found" in lowered or "does not belong" in lowered:
            _http(status.HTTP_404_NOT_FOUND, message or "Wallet transfer resource not found", exc)
        if "insufficient" in lowered:
            _http(status.HTTP_409_CONFLICT, message or "Insufficient wallet balance", exc)
        _http(status.HTTP_400_BAD_REQUEST, message or "Invalid wallet transfer request", exc)

    if isinstance(exc, ToroForgeAuthError):
        _http(status.HTTP_502_BAD_GATEWAY, "ToroForge provider authentication failed", exc)

    if isinstance(exc, (ToroForgeTimeoutError, ToroForgeUnavailableError)):
        _http(status.HTTP_503_SERVICE_UNAVAILABLE, "ToroForge provider is temporarily unavailable", exc)

    if isinstance(exc, (ToroForgeHTTPError, ToroForgeError)):
        _http(status.HTTP_502_BAD_GATEWAY, message or "ToroForge provider error", exc)

    _http(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unexpected error while processing wallet transfer", exc)
