"""Middleware wraps every attempt of every call, outermost first.

A middleware is a function `(request, context, call_next) -> response` for
`Cortex`, or an `async` one for `AsyncCortex`. It may mutate
`request.headers`, and must call `call_next(request)` exactly once to
continue. Subclass `Middleware` to write one that works with both clients;
the built-ins below do.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

Next = Callable[[httpx.Request], httpx.Response]
AsyncNext = Callable[[httpx.Request], Awaitable[httpx.Response]]


@dataclass(frozen=True, slots=True)
class RequestContext:
    operation: str
    """Stable name of the SDK method, e.g. `chat.complete`."""
    request_id: str
    """Same for every retry of the call; sent as `X-Request-ID`."""
    attempt: int
    """0 for the first try, 1 for the first retry, and so on."""
    idempotent: bool


class MiddlewareFunction(Protocol):
    def __call__(
        self, request: httpx.Request, context: RequestContext, call_next: Next, /
    ) -> httpx.Response: ...


class AsyncMiddlewareFunction(Protocol):
    def __call__(
        self, request: httpx.Request, context: RequestContext, call_next: AsyncNext, /
    ) -> Awaitable[httpx.Response]: ...


class Middleware:
    """Base for middleware usable by both `Cortex` and `AsyncCortex`.

    Override `before` and `after` for the common case, or `handle` and
    `ahandle` for full control.
    """

    def before(self, request: httpx.Request, context: RequestContext) -> None:
        """Runs before the attempt is sent."""

    def after(
        self,
        request: httpx.Request,
        context: RequestContext,
        response: httpx.Response | None,
        error: BaseException | None,
        duration: float,
    ) -> None:
        """Runs once the attempt produced a response (body possibly unread) or failed."""

    def handle(
        self, request: httpx.Request, context: RequestContext, call_next: Next
    ) -> httpx.Response:
        self.before(request, context)
        started = time.perf_counter()
        try:
            response = call_next(request)
        except BaseException as exc:
            self.after(request, context, None, exc, time.perf_counter() - started)
            raise
        self.after(request, context, response, None, time.perf_counter() - started)
        return response

    async def ahandle(
        self, request: httpx.Request, context: RequestContext, call_next: AsyncNext
    ) -> httpx.Response:
        self.before(request, context)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException as exc:
            self.after(request, context, None, exc, time.perf_counter() - started)
            raise
        self.after(request, context, response, None, time.perf_counter() - started)
        return response


class LoggingMiddleware(Middleware):
    """One line per attempt: operation, status, duration, request ID.

    Successes log at DEBUG, error statuses and failures at WARNING. Never logs
    headers or bodies, which carry credentials and user content.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("cortex")

    def after(
        self,
        request: httpx.Request,
        context: RequestContext,
        response: httpx.Response | None,
        error: BaseException | None,
        duration: float,
    ) -> None:
        extra = {
            "cortex_operation": context.operation,
            "cortex_request_id": context.request_id,
            "cortex_attempt": context.attempt,
            "cortex_duration_ms": round(duration * 1000),
        }
        if response is None:
            self.logger.warning(
                "cortex %s %s failed after %dms (attempt %d, request %s): %r",
                context.operation,
                request.url.path,
                extra["cortex_duration_ms"],
                context.attempt,
                context.request_id,
                error,
                extra=extra,
            )
            return
        level = logging.DEBUG if response.is_success else logging.WARNING
        self.logger.log(
            level,
            "cortex %s %s %d in %dms (attempt %d, request %s)",
            context.operation,
            request.url.path,
            response.status_code,
            extra["cortex_duration_ms"],
            context.attempt,
            context.request_id,
            extra={**extra, "cortex_status": response.status_code},
        )


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
    operation: str
    method: str
    url: str
    attempt: int
    request_id: str
    duration: float
    """Seconds."""
    status: int | None
    """None when no response arrived."""
    error: BaseException | None = None


class TelemetryMiddleware(Middleware):
    """Reports every attempt to your metrics pipeline. `report` must not raise."""

    def __init__(self, report: Callable[[TelemetryEvent], None]) -> None:
        self.report = report

    def after(
        self,
        request: httpx.Request,
        context: RequestContext,
        response: httpx.Response | None,
        error: BaseException | None,
        duration: float,
    ) -> None:
        self.report(
            TelemetryEvent(
                operation=context.operation,
                method=request.method,
                url=str(request.url),
                attempt=context.attempt,
                request_id=context.request_id,
                duration=duration,
                status=response.status_code if response is not None else None,
                error=error,
            )
        )


HeaderSource = Mapping[str, str] | Callable[[httpx.Request, RequestContext], Mapping[str, str]]


class HeadersMiddleware(Middleware):
    """Adds headers to every request. A callable is evaluated per attempt."""

    def __init__(self, headers: HeaderSource) -> None:
        self.headers = headers

    def before(self, request: httpx.Request, context: RequestContext) -> None:
        values = self.headers(request, context) if callable(self.headers) else self.headers
        request.headers.update(values)


@dataclass(frozen=True, slots=True)
class TraceContext:
    traceparent: str
    tracestate: str | None = None


class TracingMiddleware(Middleware):
    """W3C Trace Context propagation.

    `current` returns the active trace (for example from OpenTelemetry), which
    is propagated unchanged. Without one, each call starts a trace whose ID
    derives from the request ID, so logs on both sides join on either value;
    each attempt gets its own span ID.
    """

    def __init__(
        self, current: Callable[[], TraceContext | None] | None = None, *, sampled: bool = True
    ) -> None:
        self.current = current
        self.sampled = sampled

    def before(self, request: httpx.Request, context: RequestContext) -> None:
        active = self.current() if self.current else None
        if active is not None:
            request.headers["traceparent"] = active.traceparent
            if active.tracestate:
                request.headers["tracestate"] = active.tracestate
            return
        flags = "01" if self.sampled else "00"
        request.headers["traceparent"] = (
            f"00-{trace_id(context.request_id)}-{secrets.token_hex(8)}-{flags}"
        )


def trace_id(request_id: str) -> str:
    hex_id = request_id.removeprefix("req_").lower()
    valid = len(hex_id) == 32 and all(c in "0123456789abcdef" for c in hex_id)
    return hex_id if valid and hex_id.strip("0") else secrets.token_hex(16)
