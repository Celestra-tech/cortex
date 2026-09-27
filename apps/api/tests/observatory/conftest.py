import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.asyncio.client import PubSub

from cortex_api.core.config import Settings
from cortex_api.models.organization import Organization
from cortex_api.services.observatory.events import event_channel, subscription

# Fake providers, so completions run offline.
from ..router.conftest import (  # noqa: F401
    clock,
    cortex_router,
    providers,
    registry,
    router_override,
    sleeps,
)

HEARTBEAT_SECONDS = 0.2


@pytest.fixture
def app_settings(redis_key_prefix: str) -> Settings:
    return Settings(
        env="test",
        memory_session_key_prefix=redis_key_prefix,
        knowledge_cache_key_prefix=redis_key_prefix,
        rate_limit_key_prefix=redis_key_prefix,
        events_key_prefix=redis_key_prefix,
        events_heartbeat_seconds=HEARTBEAT_SECONDS,
        _env_file=None,
    )


class EventTap:
    """Collects what an organization's channel receives, in order."""

    def __init__(self, pubsub: PubSub) -> None:
        self.pubsub = pubsub

    async def next(self, wait: float = 2.0) -> dict[str, Any]:
        message = await self.pubsub.get_message(ignore_subscribe_messages=True, timeout=wait)
        assert message is not None, "no event arrived"
        event: dict[str, Any] = json.loads(message["data"])
        return event

    async def drain(self, wait: float = 0.2) -> list[dict[str, Any]]:
        events = []
        while message := await self.pubsub.get_message(
            ignore_subscribe_messages=True, timeout=wait
        ):
            events.append(json.loads(message["data"]))
        return events


@pytest.fixture
async def tap(
    redis: Redis, redis_key_prefix: str, organization: Organization
) -> AsyncIterator[EventTap]:
    async with subscription(redis, event_channel(redis_key_prefix, organization.id)) as pubsub:
        yield EventTap(pubsub)
