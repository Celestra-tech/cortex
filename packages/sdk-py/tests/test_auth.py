from __future__ import annotations

import re
from collections.abc import Callable

import httpx
import pytest

from cortex import (
    AuthenticationError,
    Cortex,
    CortexError,
    PermissionDeniedError,
    RetryConfig,
)

from .helpers import API_KEY, BASE_URL, ORG, Server, json_response

MODELS = json_response({"models": []})


def test_sends_bearer_key_and_client_headers(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    make_client(server).router.models()

    headers = server.last.headers
    assert headers["authorization"] == f"Bearer {API_KEY}"
    assert "x-organization-id" not in headers
    assert headers["x-cortex-client"].startswith("cortex-py/")
    assert headers["accept"] == "application/json"
    assert str(server.last.url) == f"{BASE_URL}/v1/models"


def test_sends_organization_header(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    make_client(server, organization_id=ORG).router.models()
    assert server.last.headers["x-organization-id"] == ORG


def test_credentials_fall_back_to_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_from_env")
    monkeypatch.setenv("CORTEX_ORGANIZATION_ID", ORG)
    monkeypatch.setenv("CORTEX_BASE_URL", "https://env.example/")
    server = Server(MODELS)
    with Cortex(http_client=httpx.Client(transport=httpx.MockTransport(server))) as cortex:
        assert cortex.base_url == "https://env.example"
        assert cortex.organization_id == ORG
        cortex.router.models()
    assert server.last.headers["authorization"] == "Bearer ctx_from_env"
    assert str(server.last.url) == "https://env.example/v1/models"


def test_none_disables_environment_credentials(
    monkeypatch: pytest.MonkeyPatch, make_client: Callable[..., Cortex]
) -> None:
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_from_env")
    server = Server(MODELS)
    make_client(server, api_key=None).router.models()
    assert "authorization" not in server.last.headers


def test_callable_key_is_resolved_per_attempt(make_client: Callable[..., Cortex]) -> None:
    keys = iter(["ctx_one", "ctx_two"])
    server = Server(json_response({}, 503), MODELS)
    make_client(server, api_key=lambda: next(keys)).router.models()
    assert [r.headers["authorization"] for r in server.requests] == [
        "Bearer ctx_one",
        "Bearer ctx_two",
    ]


def test_empty_callable_key_is_rejected(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    with pytest.raises(CortexError, match="empty key"):
        make_client(server, api_key=lambda: "").router.models()
    assert server.requests == []


def test_request_id_is_generated_and_stable_across_retries(
    make_client: Callable[..., Cortex],
) -> None:
    server = Server(json_response({}, 503), json_response({}, 503), MODELS)
    make_client(server).router.models()
    ids = {r.headers["x-request-id"] for r in server.requests}
    assert len(server.requests) == 3
    assert len(ids) == 1
    assert re.fullmatch(r"req_[0-9a-f]{32}", ids.pop())


def test_each_call_gets_a_new_request_id(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    cortex = make_client(server)
    cortex.router.models()
    cortex.router.models()
    assert server.requests[0].headers["x-request-id"] != server.requests[1].headers["x-request-id"]


def test_caller_supplied_request_id_and_headers(make_client: Callable[..., Cortex]) -> None:
    server = Server(MODELS)
    make_client(server, default_headers={"X-App": "demo"}).router.models(
        request_options={"request_id": "trace-123", "headers": {"X-Extra": "1"}}
    )
    headers = server.last.headers
    assert headers["x-request-id"] == "trace-123"
    assert headers["x-app"] == "demo"
    assert headers["x-extra"] == "1"


def test_authentication_error(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response(
            {"detail": "Invalid API key"},
            401,
            {"x-request-id": "srv-1", "www-authenticate": "Bearer"},
        )
    )
    with pytest.raises(AuthenticationError) as caught:
        make_client(server).router.models()
    error = caught.value
    assert error.status == 401
    assert error.detail == "Invalid API key"
    assert error.request_id == "srv-1"
    assert "router.models failed with 401: Invalid API key" in str(error)
    assert len(server.requests) == 1


def test_organization_mismatch_is_permission_denied(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "API key does not belong to organization"}, 403))
    with pytest.raises(PermissionDeniedError):
        make_client(server, organization_id=ORG).router.models()


def test_error_request_id_falls_back_to_client_id(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "nope"}, 401))
    with pytest.raises(AuthenticationError) as caught:
        make_client(server, retry=RetryConfig(max_retries=0)).router.models(
            request_options={"request_id": "mine"}
        )
    assert caught.value.request_id == "mine"
