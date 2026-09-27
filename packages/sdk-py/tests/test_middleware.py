from __future__ import annotations

import logging
import re
from collections.abc import Callable

import httpx
import pytest

from cortex import (
    AsyncCortex,
    Cortex,
    HeadersMiddleware,
    LoggingMiddleware,
    Middleware,
    RequestContext,
    TelemetryEvent,
    TelemetryMiddleware,
    TraceContext,
    TracingMiddleware,
)
from cortex.middleware import AsyncNext, Next

from .helpers import Server, json_response

MODELS = json_response({"models": []})


def test_function_middleware_runs_outermost_first_per_attempt(
    make_client: Callable[..., Cortex],
) -> None:
    calls: list[str] = []

    def outer(request: httpx.Request, context: RequestContext, call_next: Next) -> httpx.Response:
        calls.append(f"outer:{context.operation}:{context.attempt}")
        return call_next(request)

    def inner(request: httpx.Request, context: RequestContext, call_next: Next) -> httpx.Response:
        calls.append(f"inner:{context.attempt}")
        request.headers["x-inner"] = "1"
        return call_next(request)

    server = Server(json_response({}, 503), MODELS)
    make_client(server, middleware=[outer, inner]).router.models()

    assert calls == ["outer:router.models:0", "inner:0", "outer:router.models:1", "inner:1"]
    assert all(r.headers["x-inner"] == "1" for r in server.requests)


def test_middleware_can_short_circuit(make_client: Callable[..., Cortex]) -> None:
    def cached(request: httpx.Request, context: RequestContext, call_next: Next) -> httpx.Response:
        return httpx.Response(200, json={"models": []}, request=request)

    server = Server(MODELS)
    assert make_client(server, middleware=[cached]).router.models().models == []
    assert server.requests == []


def test_use_adds_middleware_after_construction(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    cortex = make_client(server)
    cortex.use(HeadersMiddleware({"X-Tenant-Region": "eu"}))
    cortex.router.models()
    assert server.last.headers["x-tenant-region"] == "eu"


def test_headers_middleware_callable(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({}, 503), MODELS)
    headers = HeadersMiddleware(lambda _req, ctx: {"X-Attempt": str(ctx.attempt)})
    make_client(server, middleware=[headers]).router.models()
    assert [r.headers["x-attempt"] for r in server.requests] == ["0", "1"]


def test_logging_middleware(
    make_client: Callable[..., Cortex], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="cortex")
    server = Server(json_response({"detail": "busy"}, 503), MODELS)
    make_client(server, middleware=[LoggingMiddleware()]).router.models(
        request_options={"request_id": "req-log"}
    )

    first, second = caplog.records
    assert first.levelno == logging.WARNING
    assert "router.models /v1/models 503" in first.getMessage()
    assert "request req-log" in first.getMessage()
    assert second.levelno == logging.DEBUG
    assert second.cortex_attempt == 1  # type: ignore[attr-defined]
    assert "ctx_" not in caplog.text


def test_logging_middleware_logs_failures(
    make_client: Callable[..., Cortex], caplog: pytest.LogCaptureFixture
) -> None:
    server = Server(httpx.ConnectError("refused"), MODELS)
    make_client(server, middleware=[LoggingMiddleware()]).router.models()
    assert "failed" in caplog.records[0].getMessage()
    assert "ConnectError" in caplog.records[0].getMessage()


def test_telemetry_middleware(make_client: Callable[..., Cortex]) -> None:
    events: list[TelemetryEvent] = []
    server = Server(httpx.ConnectError("refused"), MODELS)
    make_client(server, middleware=[TelemetryMiddleware(events.append)]).router.models()

    assert [(e.attempt, e.status) for e in events] == [(0, None), (1, 200)]
    assert isinstance(events[0].error, httpx.ConnectError)
    assert events[1].operation == "router.models"
    assert events[1].method == "GET"
    assert events[1].url.endswith("/v1/models")
    assert events[0].request_id == events[1].request_id
    assert events[1].duration >= 0


def test_tracing_derives_trace_id_from_request_id(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({}, 503), MODELS)
    make_client(server, middleware=[TracingMiddleware()]).router.models()

    parents = [r.headers["traceparent"] for r in server.requests]
    request_id = server.requests[0].headers["x-request-id"]
    for parent in parents:
        assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", parent)
        assert parent.split("-")[1] == request_id.removeprefix("req_")
    assert parents[0].split("-")[2] != parents[1].split("-")[2]


def test_tracing_with_custom_request_id_and_unsampled(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    make_client(server, middleware=[TracingMiddleware(sampled=False)]).router.models(
        request_options={"request_id": "not-hex"}
    )
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-00", server.last.headers["traceparent"])


def test_tracing_propagates_the_active_trace(make_client: Callable[..., Cortex]) -> None:
    active = TraceContext(
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", tracestate="k=v"
    )
    server = Server(MODELS)
    make_client(server, middleware=[TracingMiddleware(lambda: active)]).router.models()
    assert server.last.headers["traceparent"] == active.traceparent
    assert server.last.headers["tracestate"] == "k=v"


async def test_async_function_middleware_and_builtins(
    make_async_client: Callable[..., AsyncCortex],
) -> None:
    seen: list[int] = []

    async def record(
        request: httpx.Request, context: RequestContext, call_next: AsyncNext
    ) -> httpx.Response:
        seen.append(context.attempt)
        return await call_next(request)

    events: list[TelemetryEvent] = []
    server = Server(json_response({}, 503), MODELS)
    cortex = make_async_client(
        server,
        middleware=[record, TelemetryMiddleware(events.append), HeadersMiddleware({"X-A": "1"})],
    )
    await cortex.router.models()
    assert seen == [0, 1]
    assert [e.status for e in events] == [503, 200]
    assert server.last.headers["x-a"] == "1"


def test_custom_middleware_subclass(make_client: Callable[..., Cortex]) -> None:
    class Stamp(Middleware):
        def before(self, request: httpx.Request, context: RequestContext) -> None:
            request.headers["x-stamp"] = context.operation

    server = Server(MODELS)
    make_client(server, middleware=[Stamp()]).router.models()
    assert server.last.headers["x-stamp"] == "router.models"
