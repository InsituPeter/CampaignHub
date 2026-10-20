from collections.abc import Mapping, Sequence
from typing import ClassVar

ErrorDetail = Mapping[str, object]


class AppError(Exception):
    default_code: ClassVar[str] = "APPLICATION_ERROR"
    default_message: ClassVar[str] = "The request could not be completed."
    status_code: ClassVar[int] = 500
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        *,
        code: str | None = None,
        message: str | None = None,
        details: Sequence[ErrorDetail] | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = message or self.default_message
        self.details = [dict(detail) for detail in details or ()]
        self.retryable = self.default_retryable if retryable is None else retryable

        super().__init__(self.message)


class BadRequestError(AppError):
    default_code = "INVALID_REQUEST"
    default_message = "The request is invalid."
    status_code = 400


class AuthenticationRequiredError(AppError):
    default_code = "AUTHENTICATION_REQUIRED"
    default_message = "Authentication is required."
    status_code = 401


class ForbiddenError(AppError):
    default_code = "FORBIDDEN"
    default_message = "You are not allowed to perform this operation."
    status_code = 403


class ResourceNotFoundError(AppError):
    default_code = "RESOURCE_NOT_FOUND"
    default_message = "The requested resource was not found."
    status_code = 404


class ConflictError(AppError):
    default_code = "CONFLICT"
    default_message = "The request conflicts with the current state."
    status_code = 409


class InvalidStateError(ConflictError):
    default_code = "INVALID_STATE"
    default_message = "The resource is not in a valid state for this operation."


class RateLimitExceededError(AppError):
    default_code = "RATE_LIMIT_EXCEEDED"
    default_message = "The request rate limit has been exceeded."
    status_code = 429
    default_retryable = True


class DependencyUnavailableError(AppError):
    default_code = "DEPENDENCY_UNAVAILABLE"
    default_message = "A required service is temporarily unavailable."
    status_code = 503
    default_retryable = True
