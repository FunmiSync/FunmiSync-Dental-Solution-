class ToroForgeError(Exception):
    pass


class ToroForgeHTTPError(ToroForgeError):
    pass


class ToroForgeTimeoutError(ToroForgeHTTPError):
    pass


class ToroForgeUnavailableError(ToroForgeHTTPError):
    pass


class ToroForgeAuthError(ToroForgeError):
    pass


class ToroForgeValidationError(ToroForgeError):
    pass


class ToroForgeDuplicateNameError(ToroForgeValidationError):
    pass


class ToroForgeWalletCreationError(ToroForgeError):
    pass


class DefinitiveRejection(ToroForgeValidationError):
    """A well-formed provider response explicitly rejects a write."""
