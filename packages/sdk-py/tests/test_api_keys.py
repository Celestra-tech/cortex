from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from cortex import AsyncCortex, ConflictError, Cortex, PermissionDeniedError

from .helpers import ORG, Server, json_response

SECRET = "ctx_secret"  # noqa: S105 - a fake key
KEY: dict[str, Any] = {
    "id": "k1",
    "organization_id": ORG,
    "name": "backend",
    "prefix": "ctx_AbCdEfGh",
    "role": "member",
    "status": "active",
    "created_at": "2026-09-27T12:00:00Z",
    "last_used_at": None,
    "expires_at": None,
    "revoked_at": None,
    "rotated_from_id": None,
}


def test_create_list_rotate_revoke(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({**KEY, "secret": SECRET}, 201),
        json_response({"items": [KEY]}),
        json_response({**KEY, "id": "k2", "rotated_from_id": "k1", "secret": "ctx_new"}, 201),
        json_response({**KEY, "status": "revoked", "revoked_at": "2026-09-27T13:00:00Z"}),
    )
    cortex = make_client(server)

    created = cortex.api_keys.create(name="backend", expires_in_days=30)
    assert created.secret == SECRET
    assert json.loads(server.requests[0].content) == {
        "name": "backend",
        "role": "member",
        "expires_in_days": 30,
    }

    keys = cortex.api_keys.list(include_revoked=True)
    assert keys.items[0].prefix == "ctx_AbCdEfGh"
    assert dict(server.last.url.params) == {"include_revoked": "true"}

    rotated = cortex.api_keys.rotate("k1", grace_period_seconds=0)
    assert rotated.rotated_from_id == "k1"
    assert server.last.url.path == "/v1/api-keys/k1/rotate"
    assert json.loads(server.last.content) == {"grace_period_seconds": 0}

    revoked = cortex.api_keys.revoke("k1")
    assert server.last.method == "DELETE"
    assert revoked.status == "revoked" and revoked.revoked_at is not None


def test_minting_is_never_retried(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "unavailable"}, 503))
    with pytest.raises(Exception):  # noqa: B017 - any error, but only one attempt
        make_client(server).api_keys.create(name="x")
    assert len(server.requests) == 1


def test_role_and_last_admin_errors(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({"detail": "This action requires an admin API key"}, 403),
        json_response({"detail": "Refusing to revoke the last usable admin key"}, 409),
    )
    cortex = make_client(server)
    with pytest.raises(PermissionDeniedError):
        cortex.api_keys.list()
    with pytest.raises(ConflictError):
        cortex.api_keys.revoke("k1")


def test_provider_health_reports_unhealthy_without_raising(
    make_client: Callable[..., Cortex],
) -> None:
    body = {
        "status": "unhealthy",
        "available": 0,
        "providers": [
            {
                "name": "openai",
                "status": "circuit_open",
                "configured": True,
                "circuit_open": True,
                "consecutive_failures": 3,
                "last_error": "server_error",
            }
        ],
        "embeddings": {"space": "local/hash-256", "configured": True},
    }
    server = Server(json_response(body, 503))
    report = make_client(server).system.providers_health()
    assert report.status == "unhealthy"
    assert report.providers[0].circuit_open
    assert len(server.requests) == 1


async def test_async_api_keys(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(
        json_response({**KEY, "secret": SECRET}, 201),
        json_response({"status": "healthy", "redis": "connected", "latency_ms": 0.4}),
    )
    async with make_async_client(server) as cortex:
        created = await cortex.api_keys.create(name="backend", role="admin")
        assert created.secret == SECRET
        assert json.loads(server.requests[0].content)["role"] == "admin"
        assert (await cortex.system.redis_health()).redis == "connected"
