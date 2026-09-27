"""Pure-ASGI HTTP middleware: metrics and access logs, security headers, body limits.

Pure ASGI rather than `BaseHTTPMiddleware`, so streaming responses (SSE)
pass through untouched and nothing buffers a body.
"""

import json
import logging
import time

from fastapi import HTTPException, status
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from cortex_api.core.metrics import (
    HTTP_EXCEPTIONS,
    HTTP_IN_FLIGHT,
    HTTP_LATENCY,
    HTTP_REQUESTS,
    RATE_LIMITED,
)

access_logger = logging.getLogger("cortex_api.access")

_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_QUIET_PREFIXES = ("/health",)


def route_template(scope: Scope) -> str:
    """`/v1/conversations/{conversation_id}` rather than the raw path; bounded cardinality.

    FastAPI keeps included routers nested, so `scope["route"].path` is relative
    to its router; the effective route context carries the full template.
    """
    fastapi_scope = scope.get("fastapi")
    context = (
        fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    )
    for candidate in (context, scope.get("route")):
        path = getattr(candidate, "path_format", None) or getattr(candidate, "path", None)
        if isinstance(path, str):
            return path
    return "unmatched"


class ObservabilityMiddleware:
    """Request metrics plus one structured access-log record per request.

    The record uses Cloud Logging's `httpRequest` shape so the console renders
    it as a request entry; the query string is omitted since it may carry data.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500
        response_bytes = 0

        async def observe(message: Message) -> None:
            nonlocal status_code, response_bytes
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            await send(message)

        HTTP_IN_FLIGHT.inc()
        exception: str | None = None
        try:
            await self.app(scope, receive, observe)
        except Exception as exc:
            exception, status_code = type(exc).__name__, 500
            raise
        finally:
            HTTP_IN_FLIGHT.dec()
            elapsed = time.perf_counter() - started
            method = scope["method"] if scope["method"] in _METHODS else "OTHER"
            route = route_template(scope)
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(elapsed)
            if exception is not None:
                HTTP_EXCEPTIONS.labels(route, exception).inc()
            if status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                RATE_LIMITED.inc()
            self._log(scope, method, route, status_code, elapsed, response_bytes)

    @staticmethod
    def _log(
        scope: Scope, method: str, route: str, status_code: int, elapsed: float, size: int
    ) -> None:
        path: str = scope["path"]
        level = (
            logging.ERROR
            if status_code >= 500
            else logging.WARNING
            if status_code >= 400 and status_code != 404
            else logging.DEBUG
            if path.startswith(_QUIET_PREFIXES)
            else logging.INFO
        )
        if not access_logger.isEnabledFor(level):
            return
        headers = dict(scope["headers"])
        client = scope.get("client")
        access_logger.log(
            level,
            "%s %s %d %.1fms",
            method,
            path,
            status_code,
            elapsed * 1000,
            extra={
                "httpRequest": {
                    "requestMethod": method,
                    "requestUrl": path,
                    "status": status_code,
                    "responseSize": str(size),
                    "latency": f"{elapsed:.6f}s",
                    "userAgent": headers.get(b"user-agent", b"").decode("latin-1")[:256],
                    "remoteIp": client[0] if client else None,
                    "protocol": f"HTTP/{scope.get('http_version', '1.1')}",
                },
                "route": route,
            },
        )


class SecurityHeadersMiddleware:
    """Hardening headers for a JSON API.

    The interactive docs (development only) load assets from a CDN, so they are
    exempt from the restrictive CSP. HSTS is only sent when configured, i.e.
    behind TLS in staging and production.
    """

    def __init__(self, app: ASGIApp, *, hsts_seconds: int | None = None) -> None:
        self.app = app
        self.hsts = f"max-age={hsts_seconds}; includeSubDomains" if hsts_seconds else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "no-referrer")
                headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
                if not path.startswith("/docs"):
                    headers.setdefault(
                        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
                    )
                if path.startswith("/v1"):
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", self.hsts)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class RequestTooLargeError(HTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Request body exceeds the {limit}-byte limit",
        )


class BodySizeLimitMiddleware:
    """Rejects bodies over `max_bytes` with 413, declared or streamed (chunked).

    A declared `Content-Length` is refused before reading anything. Streamed
    bodies are counted as they arrive; the error is an `HTTPException`, so it
    surfaces as a 413 even when raised inside FastAPI's body parsing.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                await _reject(send, status.HTTP_400_BAD_REQUEST, "Invalid Content-Length")
                return
            if length > self.max_bytes:
                await _reject(send, *self._too_large())
                return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestTooLargeError(self.max_bytes)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestTooLargeError:
            if started:
                raise
            await _reject(send, *self._too_large())

    def _too_large(self) -> tuple[int, str]:
        return status.HTTP_413_CONTENT_TOO_LARGE, RequestTooLargeError(self.max_bytes).detail


async def _reject(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
