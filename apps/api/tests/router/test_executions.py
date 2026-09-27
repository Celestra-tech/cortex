import uuid
from decimal import Decimal

import httpx2
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.model_execution import ModelExecution, RoutingMode
from cortex_api.models.organization import Organization
from cortex_api.repositories.execution_repository import ExecutionFilters, ExecutionRepository
from cortex_api.services.router.base import ProviderErrorKind, ProviderName

from ..conftest import OrganizationFactory
from .fakes import FakeProvider

pytestmark = [pytest.mark.database, pytest.mark.redis]

HELLO = {"messages": [{"role": "user", "content": "Draft a status update."}]}


async def test_successful_completion_is_recorded(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    organization: Organization,
    session: AsyncSession,
) -> None:
    body = (
        await api.post(
            "/v1/chat/completions",
            json={**HELLO, "model": "claude-haiku-4-5", "metadata": {"feature": "status"}},
            headers=headers,
        )
    ).json()

    rows = await ExecutionRepository(session).list_for_organization(organization.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.completion_id == uuid.UUID(body["id"])
    assert (row.provider, row.model, row.attempt) == ("anthropic", "claude-haiku-4-5", 1)
    assert row.routing_mode is RoutingMode.PREFERRED
    assert row.success is True
    assert row.error is None and row.error_type is None
    assert (row.prompt_tokens, row.completion_tokens) == (120, 30)
    assert row.cost_estimate == Decimal("0.00027000")
    assert float(row.cost_estimate) == body["cost_estimate"]
    assert row.finish_reason == "stop"
    assert row.is_fallback is False
    assert row.latency_ms >= 0
    request_id = row.metadata_.pop("request_id")
    assert uuid.UUID(request_id).version == 7
    assert row.metadata_ == {
        "objective": "balanced",
        "routing_reason": "preferred: anthropic/claude-haiku-4-5 as requested",
        "provider_model": "claude-haiku-4-5-2026-01-01",
        "provider_request_id": "anthropic-req",
        "request": {"feature": "status"},
        "prompt": {
            "messages": 1,
            "system_messages": 0,
            "estimated_tokens": row.metadata_["prompt"]["estimated_tokens"],
            "temperature": None,
            "max_tokens": None,
        },
        "memory": None,
    }
    assert row.metadata_["prompt"]["estimated_tokens"] > 0


async def test_fallback_records_each_failure_reason(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    organization: Organization,
    session: AsyncSession,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    providers[ProviderName.OPENAI].fail(ProviderErrorKind.AUTHENTICATION, message="key revoked")
    providers[ProviderName.ANTHROPIC].fail(ProviderErrorKind.SERVER, times=2)
    body = (
        await api.post("/v1/chat/completions", json={**HELLO, "model": "gpt-4.1"}, headers=headers)
    ).json()
    assert body["provider"] == "gemini"

    rows = await ExecutionRepository(session).list_for_organization(
        organization.id, ExecutionFilters(completion_id=uuid.UUID(body["id"]))
    )
    ordered = sorted(rows, key=lambda r: r.attempt)
    assert [(r.attempt, r.provider, r.success, r.is_fallback) for r in ordered] == [
        (1, "openai", False, False),
        (2, "anthropic", False, True),
        (3, "anthropic", False, True),
        (4, "gemini", True, True),
    ]
    assert ordered[0].error == "authentication (HTTP 401): key revoked"
    assert ordered[0].error_type == "authentication"
    assert ordered[1].error_type == "server_error"
    assert all(r.prompt_tokens == 0 and r.cost_estimate == 0 for r in ordered[:3])
    assert ordered[3].prompt_tokens == 120


async def test_total_failure_is_recorded_and_reported(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    providers: dict[ProviderName, FakeProvider],
) -> None:
    for provider in providers.values():
        provider.fail(ProviderErrorKind.BAD_REQUEST, message="nope")

    response = await api.post("/v1/chat/completions", json=HELLO, headers=headers)
    assert response.status_code == 502
    body = response.json()
    assert body["detail"].startswith("All providers failed")
    assert [a["success"] for a in body["attempts"]] == [False, False, False]

    listed = (
        await api.get("/v1/executions", params={"completion_id": body["id"]}, headers=headers)
    ).json()
    assert listed["total"] == 3
    assert {item["error"] for item in listed["items"]} == {"bad_request (HTTP 400): nope"}
    assert all(item["success"] is False for item in listed["items"])


async def test_execution_endpoints_filter_paginate_and_isolate(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    organization_factory: OrganizationFactory,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    providers[ProviderName.OPENAI].fail(times=2)
    for model in ("gpt-4.1", "gemini-2.5-flash", "gemini-2.5-flash-lite"):
        await api.post("/v1/chat/completions", json={**HELLO, "model": model}, headers=headers)

    everything = (await api.get("/v1/executions", headers=headers)).json()
    assert everything["total"] == 5
    assert everything["items"][0]["model"] == "gemini-2.5-flash-lite", "newest first"
    assert everything["items"][-1]["model"] == "gpt-4.1"

    failures = (await api.get("/v1/executions", params={"success": False}, headers=headers)).json()
    assert failures["total"] == 2
    assert {i["provider"] for i in failures["items"]} == {"openai"}

    gemini = (
        await api.get("/v1/executions", params={"provider": "gemini"}, headers=headers)
    ).json()
    assert gemini["total"] == 2
    page = (
        await api.get("/v1/executions", params={"limit": 2, "offset": 4}, headers=headers)
    ).json()
    assert (len(page["items"]), page["total"], page["limit"], page["offset"]) == (1, 5, 2, 4)

    one = everything["items"][0]
    fetched = await api.get(f"/v1/executions/{one['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json() == one

    stranger = {ORGANIZATION_HEADER: str((await organization_factory()).id)}
    assert (await api.get("/v1/executions", headers=stranger)).json()["total"] == 0
    assert (await api.get(f"/v1/executions/{one['id']}", headers=stranger)).status_code == 404
    assert (await api.get(f"/v1/executions/{uuid.uuid4()}", headers=headers)).status_code == 404


async def test_executions_are_append_only_and_consistent(
    session: AsyncSession, organization: Organization
) -> None:
    repository = ExecutionRepository(session)
    row = ModelExecution(
        organization_id=organization.id,
        completion_id=uuid.uuid4(),
        attempt=1,
        provider="openai",
        model="gpt-4.1",
        routing_mode=RoutingMode.AUTO,
        latency_ms=10,
        success=True,
    )
    await repository.record_many([row])
    assert row.prompt_tokens == 0 and row.cost_estimate == 0 and row.metadata_ == {}

    with pytest.raises(TypeError):
        await repository.update(row, success=False)
    with pytest.raises(TypeError):
        await repository.delete(row)

    for invalid in (
        {"success": True, "error": "but also failed"},
        {"success": False, "error": None},
        {"success": True, "latency_ms": -1},
        {"success": True, "attempt": 0},
    ):
        values = {
            "organization_id": organization.id,
            "completion_id": uuid.uuid4(),
            "attempt": 1,
            "provider": "openai",
            "model": "gpt-4.1",
            "routing_mode": RoutingMode.AUTO,
            "latency_ms": 5,
            **invalid,
        }
        savepoint = await session.begin_nested()
        with pytest.raises(IntegrityError):
            await repository.record_many([ModelExecution(**values)])
        await savepoint.rollback()
