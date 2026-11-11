"""Translate service errors into HTTP responses in one place."""
from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.services.errors import (
    AuthenticationFailed,
    Conflict,
    Gone,
    InvalidRequest,
    NotFound,
    OperationFailed,
    PermissionDenied,
    ServiceError,
    TooManyRequests,
)

STATUS_CODES: dict[type[ServiceError], int] = {
    InvalidRequest: status.HTTP_400_BAD_REQUEST,
    AuthenticationFailed: status.HTTP_401_UNAUTHORIZED,
    PermissionDenied: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    Gone: status.HTTP_410_GONE,
    TooManyRequests: status.HTTP_429_TOO_MANY_REQUESTS,
    OperationFailed: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def status_code_for(exc: ServiceError) -> int:
    # Walk the class hierarchy so subclasses such as RegistrationConflict
    # inherit their parent's status code.
    for error_type in type(exc).__mro__:
        if error_type in STATUS_CODES:
            return STATUS_CODES[error_type]
    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    # Same body shape as FastAPI's HTTPException, so clients see no difference.
    return JSONResponse(status_code=status_code_for(exc), content={"detail": exc.detail})
