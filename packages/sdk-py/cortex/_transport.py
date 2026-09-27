"""Request building, retries, middleware, and response decoding.

`SyncTransport` and `AsyncTransport` differ only in how they wait; every
decision (what to retry, how long to back off, how to map errors) lives in
`_TransportCore` so both clients behave identically.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

import httpx

from ._validation import parse_response
from .auth import REQUEST_ID_HEADER, Credentials, create_request_id
from .errors import (
    APIError,
    CortexError,
    NetworkError,
    RequestTimeoutError,
    error_from_response,
    retry_after_seconds,
)
from .middleware import (
    AsyncMiddlewareFunction,
    Middleware,
    MiddlewareFunction,
    RequestContext,
)

Method = Literal["GET", "POST", "DELETE"]
QueryValue = str | int | float | bool | None | Sequence[str | int]


class RequestOptions(TypedDict, total=False):
    """Per-call overrides, accepted as `request_options=` by every SDK method."""

    timeout: float
    """Seconds, per attempt."""
    max_retries: int
    headers: Mapping[str, str]
    request_id: str
    """Supply your own correlation ID instead of a generated one."""


@dataclass(frozen=True, slots=True)
class RetryConfig:
    max_retries: int = 2
    """Retries after the first attempt."""
    initial_delay: float = 0.5
    """Seconds before the first retry; doubles each retry, with jitter."""
    max_delay: float = 8.0
    """Ceiling for one delay, including server `Retry-After` hints."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Call:
    operation: str
    method: Method = "GET"
    path: str
    params: Mapping[str, QueryValue] | None = None
    json: Any = None
    files: Mapping[str, tuple[str, bytes, str]] | None = None
    data: Mapping[str, str] | None = None
    headers: Mapping[str, str] | None = None
    idempotent: bool | None = None
    """Safe to repeat. Defaults to True for GET and DELETE. Only idempotent
    calls retry after timeouts, dropped connections, and 5xx; every call
    retries after 429, 503, and failures to connect."""
    accept_statuses: frozenset[int] = frozenset()
    """Statuses that return a body instead of raising (health checks answer 503)."""
    default_timeout: float | None = None
    options: RequestOptions | None = None


ALWAYS_RETRY = frozenset({429, 503})
RETRY_IF_IDEMPOTENT = frozenset({408, 500, 502, 504})
# The request never reached the server, so repeating it cannot duplicate work.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


@dataclass(kw_only=True)
class _TransportCore:
    base_url: str
    credentials: Credentials
    timeout: float
    retry: RetryConfig
    headers: Mapping[str, str]
    validate_responses: bool
    random: Callable[[], float] = field(default=random.random)

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def build(
        self, http: httpx.Client | httpx.AsyncClient, call: Call, request_id: str
    ) -> httpx.Request:
        options = call.options or {}
        headers = {"Accept": "application/json", **self.headers, **self.credentials.headers()}
        headers.update(call.headers or {})
        headers.update(options.get("headers") or {})
        headers[REQUEST_ID_HEADER] = request_id
        params = {k: v for k, v in (call.params or {}).items() if v is not None}
        return http.build_request(
            call.method,
            self.url(call.path),
            params=params or None,
            json=call.json,
            files=call.files,
            data=call.data,
            headers=headers,
            timeout=options.get("timeout", call.default_timeout or self.timeout),
        )

    def context(self, call: Call, request_id: str, attempt: int) -> RequestContext:
        return RequestContext(
            operation=call.operation,
            request_id=request_id,
            attempt=attempt,
            idempotent=self.idempotent(call),
        )

    @staticmethod
    def idempotent(call: Call) -> bool:
        return call.idempotent if call.idempotent is not None else call.method != "POST"

    def max_retries(self, call: Call) -> int:
        return (call.options or {}).get("max_retries", self.retry.max_retries)

    @staticmethod
    def request_id(call: Call) -> str:
        return (call.options or {}).get("request_id") or create_request_id()

    def transport_failure(
        self, exc: httpx.TransportError, call: Call, request_id: str
    ) -> tuple[CortexError, bool]:
        """The SDK error for a failed attempt, and whether it may be retried."""
        retryable = isinstance(exc, _NOT_SENT) or self.idempotent(call)
        if isinstance(exc, httpx.TimeoutException):
            return (
                RequestTimeoutError(f"{call.operation} timed out: {exc!r}", request_id=request_id),
                retryable,
            )
        return NetworkError(f"{call.operation} failed: {exc!r}", request_id=request_id), retryable

    def status_failure(
        self, response: httpx.Response, call: Call, request_id: str
    ) -> tuple[APIError, bool]:
        error = error_from_response(
            response.status_code, read_body(response), response.headers, request_id, call.operation
        )
        status = response.status_code
        retryable = status in ALWAYS_RETRY or (
            self.idempotent(call) and status in RETRY_IF_IDEMPOTENT
        )
        return error, retryable

    def delay(self, attempt: int, response: httpx.Response | None) -> float:
        hint = retry_after_seconds(response.headers) if response is not None else None
        exponential = self.retry.initial_delay * 2**attempt
        jittered = exponential / 2 + exponential / 2 * self.random()
        return min(self.retry.max_delay, hint if hint is not None else jittered)

    def decode[T](self, call: Call, response: httpx.Response, model: type[T]) -> T:
        return parse_response(
            call.operation,
            model,
            read_body(response),
            request_id=response.headers.get("x-request-id"),
            strict=self.validate_responses,
        )


def read_body(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


class SyncTransport(_TransportCore):
    def __init__(
        self,
        *,
        http: httpx.Client,
        middleware: list[Middleware | MiddlewareFunction],
        sleep: Callable[[float], None] = time.sleep,
        **core: Any,
    ) -> None:
        super().__init__(**core)
        self.http = http
        self.middleware = middleware  # shared with the client, so `use()` applies
        self.sleep = sleep

    def request[T](self, call: Call, model: type[T]) -> T:
        response = self.send(call)
        return self.decode(call, response, model)

    def send(self, call: Call, *, stream: bool = False) -> httpx.Response:
        """Returns the successful response; with `stream=True` its body is unread."""
        request_id = self.request_id(call)
        max_retries = self.max_retries(call)
        attempt = 0
        while True:
            request = self.build(self.http, call, request_id)
            context = self.context(call, request_id, attempt)
            response: httpx.Response | None = None
            try:
                response = self._dispatch(request, context, stream)
            except httpx.TransportError as exc:
                error, retryable = self.transport_failure(exc, call, request_id)
                if not retryable or attempt >= max_retries:
                    raise error from exc
            else:
                if response.is_success or response.status_code in call.accept_statuses:
                    return response
                if stream:
                    response.read()
                    response.close()
                error, retryable = self.status_failure(response, call, request_id)
                if not retryable or attempt >= max_retries:
                    raise error
            self.sleep(self.delay(attempt, response))
            attempt += 1

    def _dispatch(
        self, request: httpx.Request, context: RequestContext, stream: bool
    ) -> httpx.Response:
        def terminal(req: httpx.Request) -> httpx.Response:
            return self.http.send(req, stream=stream)

        handler: Callable[[httpx.Request], httpx.Response] = terminal
        for middleware in reversed(self.middleware):
            handler = _bind(middleware, context, handler)
        return handler(request)


def _bind(
    middleware: Middleware | MiddlewareFunction,
    context: RequestContext,
    call_next: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    if isinstance(middleware, Middleware):
        return lambda request: middleware.handle(request, context, call_next)
    return lambda request: middleware(request, context, call_next)


class AsyncTransport(_TransportCore):
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        middleware: list[Middleware | AsyncMiddlewareFunction],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        **core: Any,
    ) -> None:
        super().__init__(**core)
        self.http = http
        self.middleware = middleware  # shared with the client, so `use()` applies
        self.sleep = sleep

    async def request[T](self, call: Call, model: type[T]) -> T:
        response = await self.send(call)
        return self.decode(call, response, model)

    async def send(self, call: Call, *, stream: bool = False) -> httpx.Response:
        request_id = self.request_id(call)
        max_retries = self.max_retries(call)
        attempt = 0
        while True:
            request = self.build(self.http, call, request_id)
            context = self.context(call, request_id, attempt)
            response: httpx.Response | None = None
            try:
                response = await self._dispatch(request, context, stream)
            except httpx.TransportError as exc:
                error, retryable = self.transport_failure(exc, call, request_id)
                if not retryable or attempt >= max_retries:
                    raise error from exc
            else:
                if response.is_success or response.status_code in call.accept_statuses:
                    return response
                if stream:
                    await response.aread()
                    await response.aclose()
                error, retryable = self.status_failure(response, call, request_id)
                if not retryable or attempt >= max_retries:
                    raise error
            await self.sleep(self.delay(attempt, response))
            attempt += 1

    async def _dispatch(
        self, request: httpx.Request, context: RequestContext, stream: bool
    ) -> httpx.Response:
        async def terminal(req: httpx.Request) -> httpx.Response:
            return await self.http.send(req, stream=stream)

        handler: Callable[[httpx.Request], Awaitable[httpx.Response]] = terminal
        for middleware in reversed(self.middleware):
            handler = _abind(middleware, context, handler)
        return await handler(request)


def _abind(
    middleware: Middleware | AsyncMiddlewareFunction,
    context: RequestContext,
    call_next: Callable[[httpx.Request], Awaitable[httpx.Response]],
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    if isinstance(middleware, Middleware):
        return lambda request: middleware.ahandle(request, context, call_next)
    return lambda request: middleware(request, context, call_next)
