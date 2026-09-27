import re
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cortex_api.database.ids import uuid7

REQUEST_ID_HEADER = "X-Request-ID"
# Client-supplied IDs are echoed back as a response header, so only accept inert values.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

current_request_id: ContextVar[str | None] = ContextVar("cortex_request_id", default=None)
"""The ID of the request being handled, for log records emitted anywhere below."""


class RequestIdMiddleware:
    """Tags every HTTP request with an ID and echoes it as `X-Request-ID`.

    A well-formed client ID is kept, so an SDK retrying one logical call sends
    the same ID each attempt and all attempts correlate. Pure ASGI rather than
    `BaseHTTPMiddleware`, so streaming responses pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        supplied = next(
            (v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), None
        )
        request_id = supplied if supplied and _VALID_REQUEST_ID.match(supplied) else uuid7().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = current_request_id.set(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            current_request_id.reset(token)
