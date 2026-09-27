import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.message import MessageRole
from cortex_api.models.organization import Organization
from cortex_api.schemas.memory import SessionMessage
from cortex_api.services.memory.service import MemoryService
from cortex_api.services.memory.session import SessionCache, trim_messages

DAY = 86_400
_BASE_TIME = datetime(2026, 9, 27, 12, tzinfo=UTC)


def make_message(
    index: int, *, tokens: int = 1, role: MessageRole = MessageRole.USER
) -> SessionMessage:
    return SessionMessage(
        id=uuid.uuid4(),
        role=role,
        content=f"message {index}",
        token_count=tokens,
        created_at=_BASE_TIME + timedelta(seconds=index),
    )


def small_cache(
    redis: Redis, prefix: str, *, max_messages: int = 3, max_tokens: int = 100
) -> SessionCache:
    return SessionCache(
        redis,
        ttl_seconds=DAY,
        max_messages=max_messages,
        max_tokens=max_tokens,
        key_prefix=prefix,
    )


# --- Window policy (pure) ----------------------------------------------------


def test_trim_caps_message_count() -> None:
    messages = [make_message(i) for i in range(5)]
    assert trim_messages(messages, max_messages=3, max_tokens=100) == messages[2:]


def test_trim_drops_oldest_until_within_token_budget() -> None:
    messages = [make_message(i, tokens=4) for i in range(4)]
    assert trim_messages(messages, max_messages=10, max_tokens=9) == messages[2:]


def test_trim_always_keeps_newest_message() -> None:
    messages = [make_message(0, tokens=2), make_message(1, tokens=50)]
    assert trim_messages(messages, max_messages=10, max_tokens=10) == messages[1:]


# --- SessionCache against Redis --------------------------------------------


@pytest.mark.redis
async def test_append_stores_session_hash_with_ttl(
    redis: Redis, session_cache: SessionCache, redis_key_prefix: str
) -> None:
    conversation_id = uuid.uuid4()
    message = make_message(0, tokens=7)

    state = await session_cache.append(conversation_id, message)

    key = f"{redis_key_prefix}session:{conversation_id}"
    assert session_cache.key(conversation_id) == key
    assert await redis.type(key) == "hash"
    assert set(await redis.hkeys(key)) == {"messages", "token_count", "last_activity"}
    assert DAY - 5 <= await redis.ttl(key) <= DAY
    assert state.messages == [message]
    assert state.token_count == 7
    assert state.last_activity == message.created_at

    cached = await session_cache.get(conversation_id)
    assert cached == state


@pytest.mark.redis
async def test_append_trims_and_tracks_tokens(redis: Redis, redis_key_prefix: str) -> None:
    cache = small_cache(redis, redis_key_prefix, max_messages=3, max_tokens=10)
    conversation_id = uuid.uuid4()
    messages = [make_message(i, tokens=3) for i in range(5)]
    for message in messages:
        await cache.append(conversation_id, message)

    state = await cache.get(conversation_id)
    assert state is not None
    assert state.messages == messages[-3:]
    assert state.token_count == 9
    assert state.last_activity == messages[-1].created_at

    big = make_message(9, tokens=8)
    state = await cache.append(conversation_id, big)
    assert state.messages == [big]
    assert state.token_count == 8


@pytest.mark.redis
async def test_append_is_idempotent_per_message(session_cache: SessionCache) -> None:
    conversation_id = uuid.uuid4()
    message = make_message(0, tokens=2)
    await session_cache.append(conversation_id, message)
    state = await session_cache.append(conversation_id, message)
    assert state.messages == [message]
    assert state.token_count == 2


@pytest.mark.redis
async def test_concurrent_appends_are_not_lost(session_cache: SessionCache) -> None:
    conversation_id = uuid.uuid4()
    messages = [make_message(i) for i in range(20)]
    await asyncio.gather(*(session_cache.append(conversation_id, m) for m in messages))
    state = await session_cache.get(conversation_id)
    assert state is not None
    assert {m.id for m in state.messages} == {m.id for m in messages}


@pytest.mark.redis
async def test_replace_miss_and_invalidate(redis: Redis, redis_key_prefix: str) -> None:
    cache = small_cache(redis, redis_key_prefix, max_messages=2)
    conversation_id = uuid.uuid4()
    assert await cache.get(conversation_id) is None

    messages = [make_message(i, tokens=2) for i in range(3)]
    state = await cache.replace(conversation_id, messages, messages[-1].created_at)
    assert state.messages == messages[1:]
    assert state.token_count == 4
    assert await cache.get(conversation_id) == state
    assert DAY - 5 <= await cache.ttl(conversation_id) <= DAY

    empty = await cache.replace(conversation_id, [], messages[-1].created_at)
    assert empty.messages == []

    await cache.invalidate(conversation_id)
    assert await cache.get(conversation_id) is None


# --- MemoryService hot path ---------------------------------------------------


@pytest.mark.database
@pytest.mark.redis
async def test_context_served_from_cache_after_append(
    memory_service: MemoryService, session_cache: SessionCache, organization: Organization
) -> None:
    conversation = await memory_service.create_conversation(organization.id)
    first = await memory_service.append_message(
        organization.id, conversation.id, role=MessageRole.USER, content="Hello Cortex"
    )
    second = await memory_service.append_message(
        organization.id, conversation.id, role=MessageRole.ASSISTANT, content="Hello, human."
    )

    context = await memory_service.get_recent_context(organization.id, conversation.id)
    assert context.source == "cache"
    assert [m.id for m in context.messages] == [first.id, second.id]
    assert context.token_count == first.token_count + second.token_count
    assert context.last_activity == second.created_at

    tail = await memory_service.get_recent_context(organization.id, conversation.id, limit=1)
    assert [m.id for m in tail.messages] == [second.id]
    assert tail.token_count == second.token_count


@pytest.mark.database
@pytest.mark.redis
async def test_context_rehydrates_from_postgres_on_miss(
    memory_service: MemoryService, session_cache: SessionCache, organization: Organization
) -> None:
    conversation = await memory_service.create_conversation(organization.id)
    ids = [
        (
            await memory_service.append_message(
                organization.id, conversation.id, role=MessageRole.USER, content=f"turn {i}"
            )
        ).id
        for i in range(3)
    ]
    await session_cache.invalidate(conversation.id)

    rebuilt = await memory_service.get_recent_context(organization.id, conversation.id)
    assert rebuilt.source == "database"
    assert [m.id for m in rebuilt.messages] == ids

    cached = await memory_service.get_recent_context(organization.id, conversation.id)
    assert cached.source == "cache"
    assert cached.messages == rebuilt.messages
    assert DAY - 5 <= await session_cache.ttl(conversation.id) <= DAY


@pytest.mark.database
@pytest.mark.redis
async def test_empty_conversation_is_not_cached(
    memory_service: MemoryService, session_cache: SessionCache, organization: Organization
) -> None:
    conversation = await memory_service.create_conversation(organization.id)
    context = await memory_service.get_recent_context(organization.id, conversation.id)
    assert context.source == "database"
    assert context.messages == []
    assert context.last_activity == conversation.updated_at
    assert await session_cache.get(conversation.id) is None


@pytest.mark.database
@pytest.mark.redis
async def test_deleting_conversation_drops_session(
    memory_service: MemoryService, session_cache: SessionCache, organization: Organization
) -> None:
    conversation = await memory_service.create_conversation(organization.id)
    await memory_service.append_message(
        organization.id, conversation.id, role=MessageRole.USER, content="forget me"
    )
    assert await session_cache.get(conversation.id) is not None

    await memory_service.delete_conversation(organization.id, conversation.id)
    assert await session_cache.get(conversation.id) is None


@pytest.mark.database
async def test_service_degrades_gracefully_when_redis_is_down(
    session: AsyncSession, organization: Organization
) -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/15", socket_connect_timeout=0.5)
    try:
        cache = SessionCache(unreachable, ttl_seconds=DAY, max_messages=2, max_tokens=1_000)
        service = MemoryService(session, cache)
        conversation = await service.create_conversation(organization.id)
        for i in range(3):
            await service.append_message(
                organization.id, conversation.id, role=MessageRole.USER, content=f"turn {i}"
            )

        context = await service.get_recent_context(organization.id, conversation.id)
        assert context.source == "database"
        assert [m.content for m in context.messages] == ["turn 1", "turn 2"]
    finally:
        await unreachable.aclose()
