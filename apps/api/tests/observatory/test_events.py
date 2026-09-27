import uuid

import httpx2
import pytest
from redis.asyncio import Redis

from cortex_api.models.organization import Organization
from cortex_api.services.observatory.events import (
    Event,
    EventPublisher,
    EventType,
    event_channel,
)
from cortex_api.services.router.base import ProviderErrorKind, ProviderName

from ..router.fakes import FakeProvider
from .conftest import EventTap

pytestmark = [pytest.mark.database, pytest.mark.redis]

HELLO = {"messages": [{"role": "user", "content": "Summarize the launch plan."}]}
DOCUMENT = {
    "title": "Launch plan",
    "content": "# Launch plan\n\nThe Observatory ships in October. " * 20,
    "mime_type": "text/markdown",
}


class TestPublisher:
    async def test_events_reach_only_their_organization_channel(
        self, redis: Redis, redis_key_prefix: str, organization: Organization, tap: EventTap
    ) -> None:
        publisher = EventPublisher(redis, prefix=redis_key_prefix)
        assert publisher.channel(organization.id) == event_channel(
            redis_key_prefix, organization.id
        )

        await publisher.publish(uuid.uuid4(), EventType.MEMORY_STORED, {"memory_id": "other"})
        sent = await publisher.publish(organization.id, EventType.MEMORY_STORED, {"n": 1})

        received = await tap.next()
        assert received == {
            "id": str(sent.id),
            "type": "memory.stored",
            "organization_id": str(organization.id),
            "occurred_at": sent.occurred_at.isoformat(),
            "data": {"n": 1},
        }
        assert await tap.drain() == [], "another tenant's event leaked"

    async def test_without_redis_publishing_is_a_no_op(self) -> None:
        event = await EventPublisher(None).publish(uuid.uuid4(), EventType.MEMORY_STORED, {})
        assert isinstance(event, Event)

    async def test_an_unreachable_redis_never_fails_the_caller(self) -> None:
        dead = Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
        try:
            await EventPublisher(dead).publish(uuid.uuid4(), EventType.MEMORY_STORED, {})
        finally:
            await dead.aclose()

    def test_payloads_serialize_uuids_and_datetimes(self) -> None:
        event = Event(EventType.DOCUMENT_INDEXED, uuid.uuid4(), {"document_id": uuid.uuid4()})
        assert '"document_id":"' in event.to_json()


class TestCompletionEvents:
    async def test_a_completion_announces_itself_then_its_outcome(
        self, api: httpx2.AsyncClient, headers: dict[str, str], tap: EventTap
    ) -> None:
        body = (
            await api.post(
                "/v1/chat/completions",
                json={**HELLO, "model": "claude-haiku-4-5"},
                headers=headers,
            )
        ).json()

        received = await tap.next()
        completed = await tap.next()
        assert received["type"] == "request.received"
        assert received["data"]["model"] == "claude-haiku-4-5"
        assert received["data"]["messages"] == 1
        assert completed["type"] == "execution.completed"
        assert completed["data"]["request_id"] == received["data"]["request_id"]
        assert completed["data"]["completion_id"] == body["id"]
        assert completed["data"]["provider"] == "anthropic"
        assert completed["data"]["prompt_tokens"] == 120
        assert completed["data"]["fallback"] is False
        assert completed["data"]["routing_reason"]

    async def test_a_total_failure_is_announced(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        providers: dict[ProviderName, FakeProvider],
        tap: EventTap,
    ) -> None:
        for provider in providers.values():
            provider.fail(ProviderErrorKind.BAD_REQUEST, message="nope")
        body = (await api.post("/v1/chat/completions", json=HELLO, headers=headers)).json()

        events = await tap.drain()
        assert [e["type"] for e in events] == ["request.received", "execution.failed"]
        failed = events[1]["data"]
        assert failed["completion_id"] == body["id"]
        assert failed["attempts"] == 3
        assert failed["error"].startswith("All providers failed")

    async def test_a_request_rejected_before_routing_still_resolves(
        self, api: httpx2.AsyncClient, headers: dict[str, str], tap: EventTap
    ) -> None:
        response = await api.post(
            "/v1/chat/completions",
            json={**HELLO, "memory": {"conversation_id": str(uuid.uuid4())}},
            headers=headers,
        )
        assert response.status_code == 404

        events = await tap.drain()
        assert [e["type"] for e in events] == ["request.received", "execution.failed"]
        assert events[1]["data"]["request_id"] == events[0]["data"]["request_id"]
        assert events[1]["data"]["completion_id"] is None


class TestMemoryEvents:
    async def test_conversation_lifecycle_and_memories(
        self, api: httpx2.AsyncClient, headers: dict[str, str], tap: EventTap
    ) -> None:
        conversation = (
            await api.post("/v1/conversations", json={"title": "Ops"}, headers=headers)
        ).json()
        message = (
            await api.post(
                "/v1/messages",
                json={"conversation_id": conversation["id"], "role": "user", "content": "Hi"},
                headers=headers,
            )
        ).json()
        memory = (
            await api.post(
                "/v1/memories",
                json={"type": "semantic", "content": "The Observatory is monochrome."},
                headers=headers,
            )
        ).json()
        await api.delete(f"/v1/conversations/{conversation['id']}", headers=headers)

        events = await tap.drain()
        assert [e["type"] for e in events] == [
            "memory.conversation_created",
            "memory.message_appended",
            "memory.stored",
            "memory.conversation_deleted",
        ]
        appended = events[1]["data"]
        assert appended["message_id"] == message["id"]
        assert appended["role"] == "user"
        assert appended["cached"] is True
        assert events[2]["data"]["memory_id"] == memory["id"]
        assert events[3]["data"]["conversation_id"] == conversation["id"]


class TestDocumentEvents:
    async def test_ingestion_reports_progress_and_result(
        self, api: httpx2.AsyncClient, headers: dict[str, str], tap: EventTap
    ) -> None:
        document = (await api.post("/v1/documents", json=DOCUMENT, headers=headers)).json()
        await api.delete(f"/v1/documents/{document['id']}", headers=headers)

        events = await tap.drain()
        assert [e["type"] for e in events] == [
            "document.ingesting",
            "document.indexed",
            "document.deleted",
        ]
        ingesting, indexed, deleted = (e["data"] for e in events)
        assert ingesting["title"] == "Launch plan"
        assert indexed["ingest_id"] == ingesting["ingest_id"]
        assert indexed["document_id"] == document["id"]
        assert indexed["chunk_count"] == document["chunk_count"] > 0
        assert indexed["embedded_chunks"] == document["chunk_count"]
        assert deleted["document_id"] == document["id"]

    async def test_a_rejected_ingestion_is_reported(
        self, api: httpx2.AsyncClient, headers: dict[str, str], tap: EventTap
    ) -> None:
        await api.post("/v1/documents", json=DOCUMENT, headers=headers)
        duplicate = await api.post("/v1/documents", json=DOCUMENT, headers=headers)
        assert duplicate.status_code == 409

        events = await tap.drain()
        assert [e["type"] for e in events] == [
            "document.ingesting",
            "document.indexed",
            "document.ingesting",
            "document.failed",
        ]
        assert events[3]["data"]["ingest_id"] == events[2]["data"]["ingest_id"]
        assert events[3]["data"]["error_type"]
