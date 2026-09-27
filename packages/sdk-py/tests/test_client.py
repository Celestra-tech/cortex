from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cortex import AsyncCortex, Cortex, ProviderError

from .helpers import BASE_URL, ORG, Server, json_response

MODEL: dict[str, Any] = {
    "id": "openai/gpt-5-mini",
    "provider": "openai",
    "model": "gpt-5-mini",
    "display_name": "GPT-5 mini",
    "capabilities": ["chat", "json_mode"],
    "context_window": 400000,
    "max_output_tokens": 128000,
    "pricing": {"input_per_mtok": 0.25, "output_per_mtok": 2.0, "currency": "USD"},
    "quality": 0.8,
    "expected_latency_ms": 900,
    "available": True,
    "availability": "ok",
    "allowed": True,
}
EXECUTION: dict[str, Any] = {
    "id": "e1",
    "organization_id": ORG,
    "completion_id": "c1",
    "attempt": 1,
    "provider": "openai",
    "model": "gpt-5-mini",
    "routing_mode": "auto",
    "is_fallback": False,
    "latency_ms": 40.0,
    "prompt_tokens": 5,
    "completion_tokens": 3,
    "cost_estimate": 0.0001,
    "success": True,
    "finish_reason": "stop",
    "error_type": None,
    "error": None,
    "metadata": {"request_id": "req_1"},
    "created_at": "2026-09-27T12:00:00Z",
}
READY: dict[str, Any] = {
    "status": "healthy",
    "service": "cortex-api",
    "version": "1.0.0",
    "checks": {
        "postgres": {"status": "up", "latency_ms": 1.2, "error": None},
        "redis": {"status": "up", "latency_ms": 0.4, "error": None},
    },
}


def test_router_models_and_executions(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({"models": [MODEL]}),
        json_response({"items": [EXECUTION], "total": 1, "limit": 50, "offset": 0}),
        json_response(EXECUTION),
    )
    cortex = make_client(server)

    models = cortex.router.models().models
    assert models[0].pricing.output_per_mtok == 2.0
    assert models[0].allowed

    page = cortex.router.list_executions(provider="openai", success=False, completion_id="c1")
    assert page.items[0].metadata == {"request_id": "req_1"}
    assert dict(server.last.url.params) == {
        "provider": "openai",
        "success": "false",
        "completion_id": "c1",
    }

    assert cortex.router.get_execution("e1").is_fallback is False
    assert server.last.url.path == "/v1/executions/e1"


def test_readiness_returns_degraded_without_retrying(make_client: Callable[..., Cortex]) -> None:
    degraded = {
        **READY,
        "status": "degraded",
        "checks": {
            **READY["checks"],
            "redis": {"status": "down", "latency_ms": None, "error": "x"},
        },
    }
    server = Server(json_response(degraded, 503))
    readiness = make_client(server).system.readiness()
    assert readiness.status == "degraded"
    assert readiness.checks["redis"].status == "down"
    assert len(server.requests) == 1


def test_health_and_organization(make_client: Callable[..., Cortex]) -> None:
    org = {
        "id": ORG,
        "name": "Acme",
        "slug": "acme",
        "settings": {},
        "created_at": "2026-09-27T12:00:00Z",
        "updated_at": "2026-09-27T12:00:00Z",
    }
    server = Server(
        json_response({"status": "healthy", "service": "cortex-api", "version": "1.0.0"}),
        json_response(org),
    )
    cortex = make_client(server)
    assert cortex.system.health().status == "healthy"
    assert cortex.system.organization().slug == "acme"


def test_with_options_shares_the_pool_and_overrides_settings(
    make_client: Callable[..., Cortex],
) -> None:
    server = Server(json_response({}, 503))
    cortex = make_client(server, organization_id=ORG)
    fast = cortex.with_options(max_retries=0, timeout=3)
    fast._transport.sleep = cortex._transport.sleep

    with pytest.raises(ProviderError):
        fast.router.models()
    assert len(server.requests) == 1
    assert server.last.headers["x-organization-id"] == ORG
    assert server.last.extensions["timeout"]["read"] == 3
    assert fast._http is cortex._http

    fast.close()
    assert not cortex._http.is_closed


def test_context_manager_closes_only_owned_clients() -> None:
    external = httpx.Client(transport=httpx.MockTransport(Server(json_response({}))))
    with Cortex(api_key="ctx_x", base_url=BASE_URL, http_client=external):
        pass
    assert not external.is_closed
    external.close()

    with Cortex(api_key="ctx_x", base_url=BASE_URL) as owned:
        pass
    assert owned._http.is_closed


def test_base_url_trailing_slash_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Cortex(api_key=None, base_url="https://x.test/api/").base_url == "https://x.test/api"
    assert Cortex(api_key=None).base_url == "http://localhost:8000"


async def test_async_client_lifecycle(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(READY))
    cortex = make_async_client(server)
    assert (await cortex.system.readiness()).checks["postgres"].latency_ms == 1.2

    async with AsyncCortex(api_key=None, base_url=BASE_URL) as owned:
        pass
    assert owned._http.is_closed
