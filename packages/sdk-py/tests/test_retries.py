from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from cortex import (
    AsyncCortex,
    Cortex,
    InternalServerError,
    NetworkError,
    ProviderError,
    RateLimitError,
    RequestTimeoutError,
    RetryConfig,
)

from .helpers import HELLO, Server, Sleeps, completion, json_response

MODELS = json_response({"models": []})
SEARCH = {
    "query_id": "q1",
    "query": "refunds",
    "mode": "hybrid",
    "results": [],
    "context": {
        "text": "",
        "token_count": 0,
        "truncated": False,
        "citations": [],
        "confidence": {"score": 0, "level": "none"},
    },
    "metrics": {"latency_ms": 3},
}


def test_retries_503_then_succeeds(make_client: Callable[..., Cortex], sleeps: Sleeps) -> None:
    server = Server(json_response({}, 503), json_response({}, 503), MODELS)
    result = make_client(server).router.models()
    assert result.models == []
    assert len(server.requests) == 3
    assert len(sleeps) == 2


def test_gives_up_after_max_retries(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "busy"}, 503))
    with pytest.raises(ProviderError):
        make_client(server, max_retries=1).router.models()
    assert len(server.requests) == 2


def test_per_call_max_retries(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({}, 503))
    with pytest.raises(ProviderError):
        make_client(server).router.models(request_options={"max_retries": 0})
    assert len(server.requests) == 1


def test_get_retries_500_but_post_does_not(make_client: Callable[..., Cortex]) -> None:
    get = Server(json_response({}, 500), MODELS)
    make_client(get).router.models()
    assert len(get.requests) == 2

    post = Server(json_response({"detail": "boom"}, 500))
    with pytest.raises(InternalServerError):
        make_client(post).chat.complete(messages=HELLO)
    assert len(post.requests) == 1


def test_completion_is_not_retried_after_provider_failure(
    make_client: Callable[..., Cortex],
) -> None:
    body = {"id": "c1", "detail": "All providers failed", "attempts": [{"provider": "openai"}]}
    server = Server(json_response(body, 502))
    with pytest.raises(ProviderError) as caught:
        make_client(server).chat.complete(messages=HELLO)
    assert len(server.requests) == 1
    assert caught.value.completion_id == "c1"
    assert caught.value.attempts == [{"provider": "openai"}]


def test_read_only_post_retries_5xx(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({}, 502), json_response(SEARCH))
    result = make_client(server).knowledge.search("refunds")
    assert result.query_id == "q1"
    assert len(server.requests) == 2


def test_rate_limit_honors_retry_after(make_client: Callable[..., Cortex], sleeps: Sleeps) -> None:
    server = Server(
        json_response({}, 429, {"retry-after": "3"}),
        json_response({}, 429, {"retry-after-ms": "250"}),
        json_response(completion()),
    )
    make_client(server).chat.complete(messages=HELLO)
    assert sleeps == [3.0, 0.25]


def test_retry_after_is_capped(make_client: Callable[..., Cortex], sleeps: Sleeps) -> None:
    server = Server(json_response({}, 429, {"retry-after": "120"}), MODELS)
    make_client(server, retry=RetryConfig(max_delay=5)).router.models()
    assert sleeps == [5]


def test_rate_limit_error_exposes_retry_after(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "slow down"}, 429, {"retry-after": "7"}))
    with pytest.raises(RateLimitError) as caught:
        make_client(server, max_retries=0).router.models()
    assert caught.value.retry_after == 7


def test_exponential_backoff_with_jitter(
    make_client: Callable[..., Cortex], sleeps: Sleeps
) -> None:
    server = Server(json_response({}, 503))
    cortex = make_client(server, retry=RetryConfig(max_retries=4, initial_delay=1, max_delay=5))
    cortex._transport.random = lambda: 1.0
    with pytest.raises(ProviderError):
        cortex.router.models()
    assert sleeps == [1, 2, 4, 5]


def test_connect_errors_are_retried_even_for_posts(make_client: Callable[..., Cortex]) -> None:
    server = Server(httpx.ConnectError("refused"), json_response(completion()))
    make_client(server).chat.complete(messages=HELLO)
    assert len(server.requests) == 2


def test_read_timeout_is_not_retried_for_posts(make_client: Callable[..., Cortex]) -> None:
    server = Server(httpx.ReadTimeout("slow"))
    with pytest.raises(RequestTimeoutError) as caught:
        make_client(server).chat.complete(messages=HELLO)
    assert len(server.requests) == 1
    assert caught.value.request_id is not None
    assert isinstance(caught.value, NetworkError)


def test_read_timeout_is_retried_for_reads(make_client: Callable[..., Cortex]) -> None:
    server = Server(httpx.ReadTimeout("slow"), MODELS)
    make_client(server).router.models()
    assert len(server.requests) == 2


def test_dropped_connection_raises_network_error(make_client: Callable[..., Cortex]) -> None:
    server = Server(httpx.RemoteProtocolError("peer closed"))
    with pytest.raises(NetworkError, match=r"chat\.complete failed"):
        make_client(server).chat.complete(messages=HELLO)
    assert len(server.requests) == 1


def test_timeout_is_applied_per_attempt(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    cortex = make_client(server, timeout=12)
    cortex.router.models()
    assert server.last.extensions["timeout"]["read"] == 12
    cortex.router.models(request_options={"timeout": 2})
    assert server.last.extensions["timeout"]["read"] == 2


async def test_async_client_retries(
    make_async_client: Callable[..., AsyncCortex], sleeps: Sleeps
) -> None:
    server = Server(json_response({}, 429, {"retry-after": "1"}), MODELS)
    result = await make_async_client(server).router.models()
    assert result.models == []
    assert sleeps == [1.0]
    assert len({r.headers["x-request-id"] for r in server.requests}) == 1
