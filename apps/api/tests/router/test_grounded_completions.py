import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.knowledge_query import KnowledgeQuery
from cortex_api.models.message import MessageRole
from cortex_api.models.model_execution import ModelExecution
from cortex_api.services.knowledge.citations import CITATION_INSTRUCTIONS
from cortex_api.services.router.base import FinishReason, ProviderName, ProviderResponse

from .fakes import FakeProvider

pytestmark = [pytest.mark.database, pytest.mark.redis]

POLICY = """# Refund Policy

## Refunds
Annual plans can be refunded within 30 days of purchase. Refunds take 5 business days.

## Chargebacks
Chargebacks are disputed with the card network.
"""

QUESTION: dict[str, Any] = {
    "messages": [
        {"role": "system", "content": "You are a support agent."},
        {"role": "user", "content": "How long do refunds for annual plans take?"},
    ],
    "model": "gpt-4.1",
}


def answer(output: str) -> ProviderResponse:
    return ProviderResponse(
        output=output,
        prompt_tokens=200,
        completion_tokens=20,
        finish_reason=FinishReason.STOP,
        provider_model="gpt-4.1-2026-01-01",
        provider_request_id="req",
    )


@pytest.fixture
async def policy(api: httpx2.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await api.post(
        "/v1/documents",
        json={"title": "Refund Policy", "content": POLICY, "mime_type": "text/markdown"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_sources_are_injected_and_citations_tracked(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    session: AsyncSession,
    providers: dict[ProviderName, FakeProvider],
    policy: dict[str, Any],
) -> None:
    openai = providers[ProviderName.OPENAI]
    openai.script.append(answer("Refunds take 5 business days [1]."))
    response = await api.post(
        "/v1/chat/completions", json={**QUESTION, "knowledge": {"top_k": 3}}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    messages = openai.calls[-1].messages
    assert [m.role for m in messages] == [MessageRole.SYSTEM, MessageRole.SYSTEM, MessageRole.USER]
    assert messages[0].content == "You are a support agent."
    sources = messages[1].content
    assert sources.startswith(CITATION_INSTRUCTIONS)
    assert "[1] Refund Policy > Refunds\n" in sources
    assert "5 business days" in sources

    knowledge = body["knowledge"]
    assert knowledge["applied"] is True
    assert knowledge["cited"] == [1]
    assert knowledge["citations"][0]["document_id"] == policy["id"]
    assert knowledge["citations"][0]["cited"] is True
    assert all(c["cited"] is False for c in knowledge["citations"][1:])

    logged = await session.get(KnowledgeQuery, uuid.UUID(knowledge["query_id"]))
    assert logged is not None
    await session.refresh(logged)
    assert logged.completion_id == uuid.UUID(body["id"])
    assert logged.cited == [1]

    executions = (
        (
            await session.execute(
                select(ModelExecution).where(ModelExecution.completion_id == uuid.UUID(body["id"]))
            )
        )
        .scalars()
        .all()
    )
    assert executions
    assert all(e.metadata_["knowledge_query_id"] == knowledge["query_id"] for e in executions)

    metrics = (await api.get("/v1/knowledge/metrics", headers=headers)).json()["retrieval"]
    assert metrics["grounded_completions"] == 1
    assert metrics["citations_used"] == 1
    assert metrics["citation_usage_rate"] == pytest.approx(1 / metrics["citations_offered"])


async def test_low_confidence_context_is_withheld(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    providers: dict[ProviderName, FakeProvider],
    policy: dict[str, Any],
) -> None:
    openai = providers[ProviderName.OPENAI]
    openai.script.append(answer("I am not sure [1]."))
    response = await api.post(
        "/v1/chat/completions",
        json={**QUESTION, "knowledge": {"min_confidence": 1.0}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["applied"] is False
    # Markers are not attributed when no sources were given to the model.
    assert knowledge["cited"] == []
    assert knowledge["citations"] and all(c["cited"] is None for c in knowledge["citations"])
    assert [m.role for m in openai.calls[-1].messages] == [MessageRole.SYSTEM, MessageRole.USER]


async def test_empty_corpus_and_explicit_query(
    api: httpx2.AsyncClient, headers: dict[str, str], providers: dict[ProviderName, FakeProvider]
) -> None:
    response = await api.post(
        "/v1/chat/completions",
        json={**QUESTION, "knowledge": {"query": "refund window"}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    knowledge = response.json()["knowledge"]
    assert knowledge["applied"] is False
    assert knowledge["citations"] == [] and knowledge["confidence"]["level"] == "none"
    assert len(providers[ProviderName.OPENAI].calls[-1].messages) == 2


async def test_failed_completion_still_logs_the_retrieval(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    session: AsyncSession,
    providers: dict[ProviderName, FakeProvider],
    policy: dict[str, Any],
) -> None:
    for provider in providers.values():
        provider.fail(times=10)
    response = await api.post(
        "/v1/chat/completions", json={**QUESTION, "knowledge": {}}, headers=headers
    )
    assert response.status_code >= 500
    completion_id = uuid.UUID(response.json()["id"])

    [execution, *_] = (
        (
            await session.execute(
                select(ModelExecution).where(ModelExecution.completion_id == completion_id)
            )
        )
        .scalars()
        .all()
    )
    query_id = uuid.UUID(execution.metadata_["knowledge_query_id"])
    logged = await session.get(KnowledgeQuery, query_id)
    assert logged is not None and logged.completion_id is None and logged.citation_count >= 1


async def test_knowledge_options_are_validated(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    for options in ({"top_k": 0}, {"min_confidence": 2}, {"bogus": True}):
        response = await api.post(
            "/v1/chat/completions", json={**QUESTION, "knowledge": options}, headers=headers
        )
        assert response.status_code == 422
