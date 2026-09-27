from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx2
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.memory import Memory, MemoryType
from cortex_api.models.message import MessageRole
from cortex_api.models.organization import Organization
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.memory_repository import MemoryRepository
from cortex_api.services.memory.service import MemoryService

OrganizationFactory = Callable[[], Awaitable[Organization]]

pytestmark = [pytest.mark.database, pytest.mark.redis]


async def store(
    service: MemoryService,
    organization: Organization,
    content: str,
    *,
    type: MemoryType = MemoryType.SEMANTIC,
    importance: float = 0.5,
) -> Memory:
    return await service.store_memory(
        organization.id, type=type, content=content, importance=importance
    )


async def age(session: AsyncSession, memory: Memory, days: float) -> None:
    await session.execute(
        update(Memory)
        .where(Memory.id == memory.id)
        .values(created_at=Memory.created_at - timedelta(days=days))
    )


async def test_store_memory_derives_summary(
    memory_service: MemoryService, organization: Organization
) -> None:
    short = await store(memory_service, organization, "  Prefers   weekly reports.  ")
    assert short.summary == "Prefers weekly reports."

    memory = await store(
        memory_service,
        organization,
        "The customer prefers weekly reports. " + "They are sent every Monday morning. " * 10,
        type=MemoryType.PREFERENCE,
    )
    assert memory.summary == "The customer prefers weekly reports."
    assert memory.importance == 0.5
    assert memory.type is MemoryType.PREFERENCE

    explicit = await memory_service.store_memory(
        organization.id, type=MemoryType.SEMANTIC, content="Long body.", summary="Short"
    )
    assert explicit.summary == "Short"


async def test_retrieval_ranks_by_relevance_and_excludes_non_matches(
    memory_service: MemoryService, organization: Organization
) -> None:
    strong = await store(
        memory_service, organization, "Kubernetes deploys run nightly. Deploys use Helm charts."
    )
    weak = await store(memory_service, organization, "Office plants are watered on deploy day.")
    await store(memory_service, organization, "The finance team closes books quarterly.")

    ranked = await memory_service.retrieve_memories(
        organization.id, query="How are kubernetes deployments run?"
    )
    assert [r.memory.id for r in ranked] == [strong.id, weak.id]
    assert ranked[0].score > ranked[1].score > 0


async def test_importance_and_recency_break_ties(
    memory_service: MemoryService, organization: Organization, session: AsyncSession
) -> None:
    important = await store(
        memory_service, organization, "Invoices are due net 30.", importance=1.0
    )
    ordinary = await store(memory_service, organization, "Invoices are due net 30.", importance=0.2)
    stale = await store(memory_service, organization, "Invoices are due net 30.", importance=1.0)
    await age(session, stale, days=120)

    ranked = await memory_service.retrieve_memories(organization.id, query="invoices")
    assert [r.memory.id for r in ranked] == [important.id, ordinary.id, stale.id]

    # Without a query, memories are ranked by importance and recency alone.
    browsed = await memory_service.retrieve_memories(organization.id)
    assert browsed[0].memory.id == important.id
    assert browsed[-1].memory.id == stale.id


async def test_type_importance_and_limit_filters(
    memory_service: MemoryService, organization: Organization
) -> None:
    preference = await store(
        memory_service,
        organization,
        "Prefers dark mode.",
        type=MemoryType.PREFERENCE,
        importance=0.9,
    )
    await store(
        memory_service,
        organization,
        "Dark mode shipped in v2.",
        type=MemoryType.EPISODIC,
        importance=0.9,
    )
    await store(
        memory_service,
        organization,
        "Dark mode toggle lives in settings.",
        type=MemoryType.PROCEDURAL,
        importance=0.1,
    )

    by_type = await memory_service.retrieve_memories(
        organization.id, query="dark mode", types=[MemoryType.PREFERENCE]
    )
    assert [r.memory.id for r in by_type] == [preference.id]

    important = await memory_service.retrieve_memories(
        organization.id, query="dark mode", min_importance=0.5
    )
    assert len(important) == 2
    assert all(r.memory.importance >= 0.5 for r in important)

    assert len(await memory_service.retrieve_memories(organization.id, limit=1)) == 1


async def test_retrieval_is_tenant_isolated_and_hides_deleted(
    memory_service: MemoryService,
    organization: Organization,
    organization_factory: OrganizationFactory,
    session: AsyncSession,
) -> None:
    other = await organization_factory()
    await store(memory_service, other, "Secret roadmap: launch in October.")
    mine = await store(memory_service, organization, "Public roadmap: launch soon.")
    deleted = await store(memory_service, organization, "Old roadmap: launch in June.")
    await MemoryRepository(session).delete(deleted)

    ranked = await memory_service.retrieve_memories(organization.id, query="roadmap launch")
    assert [r.memory.id for r in ranked] == [mine.id]


async def test_source_message_must_belong_to_organization(
    memory_service: MemoryService,
    organization: Organization,
    organization_factory: OrganizationFactory,
) -> None:
    conversation = await memory_service.create_conversation(organization.id)
    message = await memory_service.append_message(
        organization.id, conversation.id, role=MessageRole.USER, content="I love espresso."
    )
    memory = await memory_service.store_memory(
        organization.id,
        type=MemoryType.PREFERENCE,
        content="Loves espresso.",
        source_message_id=message.id,
    )
    assert memory.source_message_id == message.id

    other = await organization_factory()
    with pytest.raises(NotFoundError):
        await memory_service.store_memory(
            other.id, type=MemoryType.PREFERENCE, content="Stolen.", source_message_id=message.id
        )


# --- HTTP ----------------------------------------------------------------------


async def test_memory_endpoints(api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
    created = await api.post(
        "/v1/memories",
        json={
            "type": "semantic",
            "content": "Billing runs on the first of each month.",
            "importance": 0.8,
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["summary"] == "Billing runs on the first of each month."

    invalid = await api.post(
        "/v1/memories", json={"type": "semantic", "content": "x", "importance": 2}, headers=headers
    )
    assert invalid.status_code == 422

    found = await api.get(
        "/v1/memories",
        params={"query": "when does billing run", "type": "semantic"},
        headers=headers,
    )
    assert found.status_code == 200
    body = found.json()
    assert body["query"] == "when does billing run"
    assert [m["id"] for m in body["memories"]] == [created.json()["id"]]
    assert body["memories"][0]["score"] > 0

    other_type = await api.get(
        "/v1/memories", params={"query": "billing", "type": "episodic"}, headers=headers
    )
    assert other_type.json()["memories"] == []


async def test_context_endpoint_combines_session_and_memories(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    await api.post(
        "/v1/memories",
        json={"type": "preference", "content": "The user prefers answers in Spanish."},
        headers=headers,
    )
    await api.post(
        "/v1/memories",
        json={"type": "semantic", "content": "Quarterly revenue grew twelve percent."},
        headers=headers,
    )
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    for role, content in [
        ("user", "Hi!"),
        ("assistant", "Hello! How can I help?"),
        ("user", "Which language do I prefer for answers?"),
    ]:
        await api.post(
            "/v1/messages",
            json={"conversation_id": conversation["id"], "role": role, "content": content},
            headers=headers,
        )

    response = await api.get(f"/v1/conversations/{conversation['id']}/context", headers=headers)
    assert response.status_code == 200
    context = response.json()
    assert context["source"] == "cache"
    assert [m["role"] for m in context["messages"]] == ["user", "assistant", "user"]
    assert context["token_count"] == sum(m["token_count"] for m in context["messages"])
    assert [m["summary"] for m in context["memories"]] == ["The user prefers answers in Spanish."]

    explicit = await api.get(
        f"/v1/conversations/{conversation['id']}/context",
        params={"query": "revenue", "limit": 1, "memory_limit": 3},
        headers=headers,
    )
    body = explicit.json()
    assert len(body["messages"]) == 1
    assert [m["summary"] for m in body["memories"]] == ["Quarterly revenue grew twelve percent."]

    without = await api.get(
        f"/v1/conversations/{conversation['id']}/context",
        params={"memory_limit": 0},
        headers={ORGANIZATION_HEADER: headers[ORGANIZATION_HEADER]},
    )
    assert without.json()["memories"] == []
