import uuid
from collections.abc import Awaitable, Callable

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.organization import Organization
from cortex_api.repositories.memory_repository import ConversationRepository, MessageRepository

OrganizationFactory = Callable[[], Awaitable[Organization]]

pytestmark = [pytest.mark.database, pytest.mark.redis]


async def test_create_and_get_conversation(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    created = await api.post("/v1/conversations", json={"title": "Onboarding"}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Onboarding"
    assert body["organization_id"] == headers[ORGANIZATION_HEADER]

    fetched = await api.get(f"/v1/conversations/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    detail = fetched.json()
    assert detail["id"] == body["id"]
    assert detail["message_count"] == 0
    assert detail["messages"] == []


async def test_messages_are_listed_in_order(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    for role, content in [("user", "Hello"), ("assistant", "Hi there"), ("user", "Help me")]:
        response = await api.post(
            "/v1/messages",
            json={
                "conversation_id": conversation["id"],
                "role": role,
                "content": content,
                "metadata": {"client": "test"},
            },
            headers=headers,
        )
        assert response.status_code == 201
        message = response.json()
        assert message["token_count"] >= 1
        assert message["metadata"] == {"client": "test"}

    detail = (await api.get(f"/v1/conversations/{conversation['id']}", headers=headers)).json()
    assert detail["message_count"] == 3
    assert [m["content"] for m in detail["messages"]] == ["Hello", "Hi there", "Help me"]

    limited = await api.get(
        f"/v1/conversations/{conversation['id']}",
        params={"message_limit": 2},
        headers=headers,
    )
    assert [m["content"] for m in limited.json()["messages"]] == ["Hi there", "Help me"]
    assert limited.json()["message_count"] == 3


async def test_append_message_touches_conversation(
    api: httpx2.AsyncClient, headers: dict[str, str], session: AsyncSession
) -> None:
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    await api.post(
        "/v1/messages",
        json={"conversation_id": conversation["id"], "role": "user", "content": "ping"},
        headers=headers,
    )
    stored = await ConversationRepository(session).get(uuid.UUID(conversation["id"]))
    assert stored is not None
    assert stored.updated_at >= stored.created_at


async def test_delete_conversation_is_soft(
    api: httpx2.AsyncClient, headers: dict[str, str], session: AsyncSession
) -> None:
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    deleted = await api.delete(f"/v1/conversations/{conversation['id']}", headers=headers)
    assert deleted.status_code == 204

    assert (
        await api.get(f"/v1/conversations/{conversation['id']}", headers=headers)
    ).status_code == 404
    row = await ConversationRepository(session).get(
        uuid.UUID(conversation["id"]), include_deleted=True
    )
    assert row is not None
    assert row.is_deleted


async def test_conversations_are_tenant_isolated(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    organization_factory: OrganizationFactory,
    session: AsyncSession,
) -> None:
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    other = {ORGANIZATION_HEADER: str((await organization_factory()).id)}

    assert (
        await api.get(f"/v1/conversations/{conversation['id']}", headers=other)
    ).status_code == 404
    assert (
        await api.get(f"/v1/conversations/{conversation['id']}/context", headers=other)
    ).status_code == 404

    posted = await api.post(
        "/v1/messages",
        json={"conversation_id": conversation["id"], "role": "user", "content": "intrusion"},
        headers=other,
    )
    assert posted.status_code == 404
    assert (
        await MessageRepository(session).count_for_conversation(uuid.UUID(conversation["id"])) == 0
    )


async def test_request_validation(api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
    assert (await api.post("/v1/conversations", json={})).status_code == 401
    unknown_org = {ORGANIZATION_HEADER: str(uuid.uuid4())}
    assert (await api.post("/v1/conversations", json={}, headers=unknown_org)).status_code == 404
    assert (await api.get(f"/v1/conversations/{uuid.uuid4()}", headers=headers)).status_code == 404

    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    for payload in (
        {"conversation_id": conversation["id"], "role": "user", "content": ""},
        {"conversation_id": conversation["id"], "role": "robot", "content": "hi"},
    ):
        assert (await api.post("/v1/messages", json=payload, headers=headers)).status_code == 422
