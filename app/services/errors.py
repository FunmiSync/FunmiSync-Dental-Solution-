"""Expected business-rule failures raised by services.

Services stay independent of HTTP. They raise one of these errors, and
app/controllers/errors.py turns it into the matching HTTP response. The
message is returned to the client as ``detail``, so keep it free of secrets.
"""


class ServiceError(Exception):
    """Base class for an expected failure with a client-safe message."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class InvalidRequest(ServiceError):
    """The request breaks a business rule (HTTP 400)."""


class AuthenticationFailed(ServiceError):
    """The caller's identity could not be accepted (HTTP 401)."""


class PermissionDenied(ServiceError):
    """The caller is known but not allowed to do this (HTTP 403)."""


class NotFound(ServiceError):
    """The target resource does not exist (HTTP 404)."""


class Conflict(ServiceError):
    """The request conflicts with the current state (HTTP 409)."""


class Gone(ServiceError):
    """The resource existed but can no longer be used (HTTP 410)."""


class TooManyRequests(ServiceError):
    """The caller hit a rate limit (HTTP 429)."""


class OperationFailed(ServiceError):
    """An internal step failed after validation passed (HTTP 500)."""
