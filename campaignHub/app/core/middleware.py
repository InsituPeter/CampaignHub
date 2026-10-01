import re
from typing import TypeGuard

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_context import (
    generate_request_id,
    reset_request_id,
    set_request_id,
)

REQUEST_ID_HEADER = "X-Request-ID"

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")


def is_valid_request_id(value: str | None) -> TypeGuard[str]:
    if value is None:
        return False

    return _REQUEST_ID_PATTERN.fullmatch(value) is not None


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        supplied_request_id = request_headers.get(REQUEST_ID_HEADER)

        request_id = (
            supplied_request_id
            if is_valid_request_id(supplied_request_id)
            else generate_request_id()
        )

        scope.setdefault("state", {})["request_id"] = request_id
        context_token = set_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id

            await send(message)

        try:
            await self.app(
                scope,
                receive,
                send_with_request_id,
            )
        finally:
            reset_request_id(context_token)
