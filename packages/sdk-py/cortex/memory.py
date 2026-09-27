from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from ._validation import validate_params
from .models import (
    Conversation,
    ConversationContext,
    ConversationDetail,
    ConversationParams,
    ConversationSummary,
    Memory,
    MemoryParams,
    MemorySearchResult,
    MemoryType,
    Message,
    MessageParams,
    Metadata,
    Page,
    Role,
)

ConversationPage = Page[ConversationSummary]


def _drop_none(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def _create_conversation(title: str | None, options: RequestOptions | None) -> Call:
    op = "memory.create_conversation"
    body = validate_params(op, ConversationParams, _drop_none(title=title))
    return Call(operation=op, method="POST", path="/v1/conversations", json=body, options=options)


def _list_conversations(
    limit: int | None, offset: int | None, options: RequestOptions | None
) -> Call:
    return Call(
        operation="memory.list_conversations",
        path="/v1/conversations",
        params={"limit": limit, "offset": offset},
        options=options,
    )


def _get_conversation(id: str, message_limit: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="memory.get_conversation",
        path=f"/v1/conversations/{segment(id)}",
        params={"message_limit": message_limit},
        options=options,
    )


def _delete_conversation(id: str, options: RequestOptions | None) -> Call:
    return Call(
        operation="memory.delete_conversation",
        method="DELETE",
        path=f"/v1/conversations/{segment(id)}",
        options=options,
    )


def _add_message(
    conversation_id: str,
    role: Role,
    content: str,
    metadata: Metadata | None,
    options: RequestOptions | None,
) -> Call:
    op = "memory.add_message"
    body = validate_params(
        op,
        MessageParams,
        _drop_none(conversation_id=conversation_id, role=role, content=content, metadata=metadata),
    )
    return Call(operation=op, method="POST", path="/v1/messages", json=body, options=options)


def _get_context(
    conversation_id: str,
    limit: int | None,
    query: str | None,
    memory_limit: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="memory.get_context",
        path=f"/v1/conversations/{segment(conversation_id)}/context",
        params={"limit": limit, "query": query, "memory_limit": memory_limit},
        options=options,
    )


def _store_memory(
    type: MemoryType,
    content: str,
    summary: str | None,
    importance: float | None,
    source_message_id: str | None,
    options: RequestOptions | None,
) -> Call:
    op = "memory.store_memory"
    body = validate_params(
        op,
        MemoryParams,
        _drop_none(
            type=type,
            content=content,
            summary=summary,
            importance=importance,
            source_message_id=source_message_id,
        ),
    )
    return Call(operation=op, method="POST", path="/v1/memories", json=body, options=options)


def _search_memories(
    query: str | None,
    types: Sequence[MemoryType] | None,
    min_importance: float | None,
    limit: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="memory.search_memories",
        path="/v1/memories",
        params={
            "query": query,
            "type": list(types) if types else None,
            "min_importance": min_importance,
            "limit": limit,
        },
        options=options,
    )


class MemoryResource(SyncAPIResource):
    """Conversations (hot Redis sessions backed by Postgres) and long-term memories."""

    def create_conversation(
        self, *, title: str | None = None, request_options: RequestOptions | None = None
    ) -> Conversation:
        return self._transport.request(_create_conversation(title, request_options), Conversation)

    def list_conversations(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationPage:
        """Most recently active first, with message counts and hot-session state."""
        return self._transport.request(
            _list_conversations(limit, offset, request_options), ConversationPage
        )

    def iter_conversations(self, *, page_size: int = 100) -> Iterator[ConversationSummary]:
        """Every conversation, fetched page by page as you iterate."""
        return paginate(
            lambda limit, offset: self.list_conversations(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    def get_conversation(
        self,
        id: str,
        *,
        message_limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationDetail:
        return self._transport.request(
            _get_conversation(id, message_limit, request_options), ConversationDetail
        )

    def delete_conversation(
        self, id: str, *, request_options: RequestOptions | None = None
    ) -> None:
        self._transport.send(_delete_conversation(id, request_options))

    def add_message(
        self,
        *,
        conversation_id: str,
        role: Role,
        content: str,
        metadata: Metadata | None = None,
        request_options: RequestOptions | None = None,
    ) -> Message:
        """Appends to the conversation and its hot session."""
        call = _add_message(conversation_id, role, content, metadata, request_options)
        return self._transport.request(call, Message)

    def get_context(
        self,
        conversation_id: str,
        *,
        limit: int | None = None,
        query: str | None = None,
        memory_limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationContext:
        """The hot session window plus relevant long-term memories, ready for
        the next model turn. `query` defaults to the latest user message."""
        call = _get_context(conversation_id, limit, query, memory_limit, request_options)
        return self._transport.request(call, ConversationContext)

    def store_memory(
        self,
        *,
        type: MemoryType,
        content: str,
        summary: str | None = None,
        importance: float | None = None,
        source_message_id: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> Memory:
        call = _store_memory(type, content, summary, importance, source_message_id, request_options)
        return self._transport.request(call, Memory)

    def search_memories(
        self,
        *,
        query: str | None = None,
        types: Sequence[MemoryType] | None = None,
        min_importance: float | None = None,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> MemorySearchResult:
        """Ranked by relevance, importance, and recency."""
        call = _search_memories(query, types, min_importance, limit, request_options)
        return self._transport.request(call, MemorySearchResult)


class AsyncMemoryResource(AsyncAPIResource):
    async def create_conversation(
        self, *, title: str | None = None, request_options: RequestOptions | None = None
    ) -> Conversation:
        return await self._transport.request(
            _create_conversation(title, request_options), Conversation
        )

    async def list_conversations(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationPage:
        return await self._transport.request(
            _list_conversations(limit, offset, request_options), ConversationPage
        )

    def iter_conversations(self, *, page_size: int = 100) -> AsyncIterator[ConversationSummary]:
        return apaginate(
            lambda limit, offset: self.list_conversations(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    async def get_conversation(
        self,
        id: str,
        *,
        message_limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationDetail:
        return await self._transport.request(
            _get_conversation(id, message_limit, request_options), ConversationDetail
        )

    async def delete_conversation(
        self, id: str, *, request_options: RequestOptions | None = None
    ) -> None:
        await self._transport.send(_delete_conversation(id, request_options))

    async def add_message(
        self,
        *,
        conversation_id: str,
        role: Role,
        content: str,
        metadata: Metadata | None = None,
        request_options: RequestOptions | None = None,
    ) -> Message:
        call = _add_message(conversation_id, role, content, metadata, request_options)
        return await self._transport.request(call, Message)

    async def get_context(
        self,
        conversation_id: str,
        *,
        limit: int | None = None,
        query: str | None = None,
        memory_limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ConversationContext:
        call = _get_context(conversation_id, limit, query, memory_limit, request_options)
        return await self._transport.request(call, ConversationContext)

    async def store_memory(
        self,
        *,
        type: MemoryType,
        content: str,
        summary: str | None = None,
        importance: float | None = None,
        source_message_id: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> Memory:
        call = _store_memory(type, content, summary, importance, source_message_id, request_options)
        return await self._transport.request(call, Memory)

    async def search_memories(
        self,
        *,
        query: str | None = None,
        types: Sequence[MemoryType] | None = None,
        min_importance: float | None = None,
        limit: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> MemorySearchResult:
        call = _search_memories(query, types, min_importance, limit, request_options)
        return await self._transport.request(call, MemorySearchResult)
