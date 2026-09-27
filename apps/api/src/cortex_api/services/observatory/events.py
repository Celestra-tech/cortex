"""Real-time operational events, fanned out over Redis pub/sub.

Services publish after their transaction commits, so subscribers never see
work that was rolled back. Publishing is best effort: an event that cannot be
delivered is logged and dropped, and never fails the request that produced
it. Each organization has its own channel, so a subscriber only ever receives
its own tenant's activity. Pub/sub keeps no history; a client that reconnects
should reload current state rather than expect missed events to be replayed.
"""

import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from cortex_api.core.config import Settings
from cortex_api.database.ids import uuid7

logger = logging.getLogger(__name__)

SUBSCRIBE_TIMEOUT_SECONDS = 5.0


class EventType(StrEnum):
    REQUEST_RECEIVED = "request.received"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    DOCUMENT_INGESTING = "document.ingesting"
    DOCUMENT_INDEXED = "document.indexed"
    DOCUMENT_FAILED = "document.failed"
    DOCUMENT_DELETED = "document.deleted"
    CONVERSATION_CREATED = "memory.conversation_created"
    CONVERSATION_DELETED = "memory.conversation_deleted"
    MESSAGE_APPENDED = "memory.message_appended"
    MEMORY_STORED = "memory.stored"


@dataclass(frozen=True, slots=True)
class Event:
    type: EventType
    organization_id: uuid.UUID
    data: dict[str, Any]
    id: uuid.UUID = field(default_factory=uuid7)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": str(self.id),
                "type": str(self.type),
                "organization_id": str(self.organization_id),
                "occurred_at": self.occurred_at.isoformat(),
                "data": self.data,
            },
            default=str,
            separators=(",", ":"),
        )


def event_channel(prefix: str, organization_id: uuid.UUID) -> str:
    return f"{prefix}events:{organization_id}"


class EventPublisher:
    """Publishes events for one deployment. A publisher without Redis is a no-op."""

    def __init__(self, redis: Redis | None, *, prefix: str = "") -> None:
        self.redis = redis
        self.prefix = prefix

    @classmethod
    def from_settings(cls, redis: Redis | None, settings: Settings) -> Self:
        return cls(redis, prefix=settings.events_key_prefix)

    def channel(self, organization_id: uuid.UUID) -> str:
        return event_channel(self.prefix, organization_id)

    async def publish(
        self, organization_id: uuid.UUID, type: EventType, data: dict[str, Any]
    ) -> Event:
        event = Event(type=type, organization_id=organization_id, data=data)
        if self.redis is None:
            return event
        try:
            await self.redis.publish(self.channel(organization_id), event.to_json())
        except (RedisError, OSError) as exc:
            logger.warning("Dropped %s event for %s: %r", type, organization_id, exc)
        return event


@asynccontextmanager
async def subscription(redis: Redis, channel: str) -> AsyncIterator[PubSub]:
    """Yields once Redis has confirmed the subscription, so nothing published after is missed.

    Read with `get_message(ignore_subscribe_messages=True, ...)`.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        while True:
            message = await pubsub.get_message(timeout=SUBSCRIBE_TIMEOUT_SECONDS)
            if message is None:
                raise RedisTimeoutError(f"no subscribe confirmation for {channel}")
            if message["type"] == "subscribe":
                break
        yield pubsub
    finally:
        # Cleanup on a dead connection must not mask the original error.
        with contextlib.suppress(RedisError, OSError):
            await pubsub.unsubscribe(channel)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
