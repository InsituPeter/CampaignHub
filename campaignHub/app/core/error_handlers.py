import logging
from collections.abc import Mapping
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.exceptions import AppError
from app.core.middleware import REQUEST_ID_HEADER
from app.core.request_context import generate_request_id, get_request_id

logger = logging.getLogger(__name__)

_HTTP_ERROR_CODES = {
    status.HTTP_400_BAD_REQUEST: "INVALID_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "AUTHENTICATION_REQUIRED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "ROUTE_NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMIT_EXCEEDED",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


def _get_request_id(request: Request) -> str:
    state_request_id = getattr(request.state, "request_id", None)

    if isinstance(state_request_id, str):
        return state_request_id

    return get_request_id() or generate_request_id()


def _error_response(
    *,
    request_id: str,
    code: str,
    message: str,
    status_code: int,
    details: list[dict[str, object]] | None = None,
    retryable: bool = False,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id

    return JSONResponse(
        status_code=status_code,
        headers=response_headers,
        content={
            "error": {
                "code": code,
                "message": message,
                "status": status_code,
                "request_id": request_id,
                "details": details or [],
                "retryable": retryable,
            }
        },
    )


async def handle_app_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    error = cast(AppError, exc)
    request_id = _get_request_id(request)

    return _error_response(
        request_id=request_id,
        code=error.code,
        message=error.message,
        status_code=error.status_code,
        details=error.details,
        retryable=error.retryable,
    )


async def handle_validation_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    validation_error = cast(RequestValidationError, exc)
    request_id = _get_request_id(request)

    details = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in validation_error.errors()
    ]

    return _error_response(
        request_id=request_id,
        code="VALIDATION_ERROR",
        message="The request contains invalid data.",
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        details=details,
    )


async def handle_http_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    http_error = cast(HTTPException, exc)
    request_id = _get_request_id(request)

    message = (
        http_error.detail
        if isinstance(http_error.detail, str)
        else "The request could not be completed."
    )

    return _error_response(
        request_id=request_id,
        code=_HTTP_ERROR_CODES.get(
            http_error.status_code,
            "HTTP_ERROR",
        ),
        message=message,
        status_code=http_error.status_code,
        headers=http_error.headers,
        retryable=(
            http_error.status_code
            in {
                status.HTTP_429_TOO_MANY_REQUESTS,
                status.HTTP_503_SERVICE_UNAVAILABLE,
            }
        ),
    )


async def handle_unexpected_error(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    request_id = _get_request_id(request)

    logger.error(
        "Unhandled application exception",
        extra={"request_id": request_id},
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return _error_response(
        request_id=request_id,
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected error occurred.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        retryable=False,
    )


def register_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(
        AppError,
        handle_app_error,
    )
    application.add_exception_handler(
        RequestValidationError,
        handle_validation_error,
    )
    application.add_exception_handler(
        HTTPException,
        handle_http_error,
    )
    application.add_exception_handler(
        Exception,
        handle_unexpected_error,
    )
