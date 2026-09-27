import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Self

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.models.conversation import Conversation
from cortex_api.models.memory import Memory, MemoryType
from cortex_api.models.message import Message, MessageRole
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.memory_repository import (
    ConversationRepository,
    MemoryRepository,
    MessageRepository,
)
from cortex_api.schemas.memory import SessionMessage, SessionState
from cortex_api.services.memory.encoder import MemoryEncoder
from cortex_api.services.memory.retrieval import MemoryQuery, RankedMemory, RetrievalPolicy
from cortex_api.services.memory.session import SessionCache
from cortex_api.services.observatory.events import EventPublisher, EventType

logger = logging.getLogger(__name__)

ContextSource = Literal["cache", "database"]


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    conversation: Conversation
    message_count: int
    hot: bool | None
    """Whether a Redis session exists; None when Redis could not be checked."""


@dataclass(frozen=True, slots=True)
class RecentContext:
    conversation_id: uuid.UUID
    messages: list[SessionMessage]
    token_count: int
    last_activity: datetime
    source: ContextSource


class MemoryService:
    """Conversations, their hot session window, and long-term memories.

    Owns the transaction boundary: each public write commits. Redis failures
    never fail a request; the session is rebuilt from Postgres on the next read.
    """

    def __init__(
        self,
        session: AsyncSession,
        cache: SessionCache,
        *,
        encoder: MemoryEncoder | None = None,
        retrieval: RetrievalPolicy | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self.session = session
        self.cache = cache
        self.encoder = encoder or MemoryEncoder()
        self.retrieval = retrieval or RetrievalPolicy()
        self.events = events or EventPublisher(None)
        self.conversations = ConversationRepository(session)
        self.messages = MessageRepository(session)
        self.memories = MemoryRepository(session)

    @classmethod
    def from_settings(cls, session: AsyncSession, redis: Redis, settings: Settings) -> Self:
        return cls(
            session,
            SessionCache.from_settings(redis, settings),
            retrieval=RetrievalPolicy(half_life_days=settings.memory_retrieval_half_life_days),
            events=EventPublisher.from_settings(redis, settings),
        )

    # --- Conversations -----------------------------------------------------

    async def create_conversation(
        self, organization_id: uuid.UUID, *, title: str | None = None
    ) -> Conversation:
        conversation = await self.conversations.create(organization_id=organization_id, title=title)
        await self.session.commit()
        await self.events.publish(
            organization_id,
            EventType.CONVERSATION_CREATED,
            {"conversation_id": conversation.id, "title": conversation.title},
        )
        return conversation

    async def list_conversations(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[ConversationSummary], int]:
        """Most recently active first, each with its message count and session state."""
        rows = await self.conversations.list_with_counts(
            organization_id, limit=limit, offset=offset
        )
        total = await self.conversations.count_for_organization(organization_id)
        hot = await self._cache_presence([conversation.id for conversation, _ in rows])
        summaries = [
            ConversationSummary(conversation, count, hot.get(conversation.id) if hot else None)
            for conversation, count in rows
        ]
        return summaries, total

    async def get_conversation(
        self, organization_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> Conversation:
        conversation = await self.conversations.get_for_organization(
            organization_id, conversation_id
        )
        if conversation is None:
            raise NotFoundError(Conversation, conversation_id)
        return conversation

    async def list_messages(
        self, organization_id: uuid.UUID, conversation_id: uuid.UUID, *, limit: int
    ) -> tuple[list[Message], int]:
        """The newest `limit` messages (oldest first) and the total count."""
        await self.get_conversation(organization_id, conversation_id)
        messages = await self.messages.list_recent(conversation_id, limit=limit)
        total = await self.messages.count_for_conversation(conversation_id)
        return messages, total

    async def delete_conversation(
        self, organization_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> None:
        conversation = await self.get_conversation(organization_id, conversation_id)
        await self.conversations.delete(conversation)
        await self.session.commit()
        await self._cache_invalidate(conversation_id)
        await self.events.publish(
            organization_id,
            EventType.CONVERSATION_DELETED,
            {"conversation_id": conversation_id},
        )

    # --- Messages ----------------------------------------------------------

    async def append_message(
        self,
        organization_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        conversation = await self.get_conversation(organization_id, conversation_id)
        message = await self.messages.create(
            conversation_id=conversation_id,
            role=role,
            content=content,
            token_count=self.encoder.count_tokens(content),
            metadata_=metadata or {},
        )
        await self.conversations.update(conversation, updated_at=datetime.now(UTC))
        await self.session.commit()
        # Only cache after commit, so the hot window never shows a rolled-back message.
        cached = await self._cache_append(message)
        await self.events.publish(
            organization_id,
            EventType.MESSAGE_APPENDED,
            {
                "conversation_id": conversation_id,
                "message_id": message.id,
                "role": str(role),
                "token_count": message.token_count,
                "cached": cached,
            },
        )
        return message

    # --- Hot context -------------------------------------------------------

    async def get_recent_context(
        self,
        organization_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        limit: int | None = None,
    ) -> RecentContext:
        conversation = await self.get_conversation(organization_id, conversation_id)

        cached = await self._cache_get(conversation_id)
        if cached is not None:
            return self._context(
                conversation_id, cached.messages, cached.last_activity, "cache", limit
            )

        rows = await self.messages.list_recent(conversation_id, limit=self.cache.max_messages)
        window = self.cache.trim([self.encoder.to_session_message(row) for row in rows])
        last_activity = window[-1].created_at if window else conversation.updated_at
        if window:
            await self._cache_replace(conversation_id, window, last_activity)
        return self._context(conversation_id, window, last_activity, "database", limit)

    # --- Persistent memory -------------------------------------------------

    async def store_memory(
        self,
        organization_id: uuid.UUID,
        *,
        type: MemoryType,
        content: str,
        summary: str | None = None,
        importance: float = 0.5,
        source_message_id: uuid.UUID | None = None,
    ) -> Memory:
        if source_message_id is not None:
            source = await self.messages.get_for_organization(organization_id, source_message_id)
            if source is None:
                raise NotFoundError(Message, source_message_id)

        memory = await self.memories.create(
            organization_id=organization_id,
            type=type,
            content=content,
            summary=summary or self.encoder.summarize(content),
            importance=importance,
            source_message_id=source_message_id,
        )
        await self.session.commit()
        await self.events.publish(
            organization_id,
            EventType.MEMORY_STORED,
            {
                "memory_id": memory.id,
                "type": str(memory.type),
                "summary": memory.summary,
                "importance": memory.importance,
            },
        )
        return memory

    async def retrieve_memories(
        self,
        organization_id: uuid.UUID,
        *,
        query: str | None = None,
        types: Iterable[MemoryType] | None = None,
        min_importance: float = 0.0,
        limit: int = 10,
    ) -> Sequence[RankedMemory]:
        memory_query = MemoryQuery(
            text=query,
            types=frozenset(types) if types else None,
            min_importance=min_importance,
            limit=limit,
        )
        return await self.memories.search(organization_id, memory_query, self.retrieval)

    # --- Internals ---------------------------------------------------------

    @staticmethod
    def _context(
        conversation_id: uuid.UUID,
        messages: list[SessionMessage],
        last_activity: datetime,
        source: ContextSource,
        limit: int | None,
    ) -> RecentContext:
        window = messages[-limit:] if limit else messages
        return RecentContext(
            conversation_id=conversation_id,
            messages=window,
            token_count=sum(message.token_count for message in window),
            last_activity=last_activity,
            source=source,
        )

    async def _cache_get(self, conversation_id: uuid.UUID) -> SessionState | None:
        try:
            return await self.cache.get(conversation_id)
        except RedisError as exc:
            logger.warning("Session cache read failed for %s: %r", conversation_id, exc)
            return None

    async def _cache_append(self, message: Message) -> bool:
        try:
            await self.cache.append(
                message.conversation_id, self.encoder.to_session_message(message)
            )
        except RedisError as exc:
            logger.warning("Session cache append failed for %s: %r", message.conversation_id, exc)
            # A partially applied session would silently miss this message; drop it instead.
            await self._cache_invalidate(message.conversation_id)
            return False
        return True

    async def _cache_presence(self, conversation_ids: list[uuid.UUID]) -> dict[uuid.UUID, bool]:
        """Which conversations have a hot session; empty when Redis is unreachable."""
        try:
            return await self.cache.exists_many(conversation_ids)
        except RedisError as exc:
            logger.warning("Session presence check failed: %r", exc)
            return {}

    async def _cache_replace(
        self,
        conversation_id: uuid.UUID,
        messages: list[SessionMessage],
        last_activity: datetime,
    ) -> None:
        try:
            await self.cache.replace(conversation_id, messages, last_activity)
        except RedisError as exc:
            logger.warning("Session cache rebuild failed for %s: %r", conversation_id, exc)

    async def _cache_invalidate(self, conversation_id: uuid.UUID) -> None:
        try:
            await self.cache.invalidate(conversation_id)
        except RedisError as exc:
            logger.warning("Session cache invalidate failed for %s: %r", conversation_id, exc)
