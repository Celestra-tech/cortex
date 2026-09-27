from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from cortex import AsyncCortex, Cortex, NotFoundError, ValidationError

from .helpers import ORG, Server, json_response

NOW = "2026-09-27T12:00:00Z"
CONVERSATION = {
    "id": "conv-1",
    "organization_id": ORG,
    "title": "Support",
    "created_at": NOW,
    "updated_at": NOW,
}
MESSAGE = {
    "id": "msg-1",
    "conversation_id": "conv-1",
    "role": "user",
    "content": "Where is my refund?",
    "token_count": 6,
    "metadata": {},
    "created_at": NOW,
}
MEMORY = {
    "id": "mem-1",
    "organization_id": ORG,
    "type": "preference",
    "summary": "Prefers email",
    "content": "The customer prefers email follow-ups.",
    "importance": 0.8,
    "source_message_id": None,
    "created_at": NOW,
    "updated_at": NOW,
}


def summaries(start: int, count: int) -> list[dict[str, Any]]:
    return [
        {**CONVERSATION, "id": f"conv-{i}", "message_count": i, "session": "hot"}
        for i in range(start, start + count)
    ]


def test_create_conversation(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(CONVERSATION, 201))
    conversation = make_client(server).memory.create_conversation(title="Support")
    assert conversation.id == "conv-1"
    assert server.last.method == "POST"
    assert server.last.url.path == "/v1/conversations"
    assert server.body() == {"title": "Support"}


def test_create_conversation_without_title_sends_empty_body(
    make_client: Callable[..., Cortex],
) -> None:
    server = Server(json_response({**CONVERSATION, "title": None}, 201))
    assert make_client(server).memory.create_conversation().title is None
    assert server.body() == {}


def test_add_message(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(MESSAGE, 201))
    message = make_client(server).memory.add_message(
        conversation_id="conv-1",
        role="user",
        content="Where is my refund?",
        metadata={"channel": "chat"},
    )
    assert message.token_count == 6
    assert server.last.url.path == "/v1/messages"
    assert server.body() == {
        "conversation_id": "conv-1",
        "role": "user",
        "content": "Where is my refund?",
        "metadata": {"channel": "chat"},
    }


def test_add_message_validates(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(MESSAGE, 201))
    with pytest.raises(ValidationError) as caught:
        make_client(server).memory.add_message(
            conversation_id="conv-1",
            role="narrator",  # type: ignore[arg-type]
            content="",
        )
    assert {i.path for i in caught.value.issues} == {"role", "content"}
    assert server.requests == []


def test_list_and_iterate_conversations(make_client: Callable[..., Cortex]) -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        items = summaries(offset, 2 if offset < 4 else 1)
        return json_response({"items": items, "total": 5, "limit": 2, "offset": offset})

    server = Server(pages)
    cortex = make_client(server)
    page = cortex.memory.list_conversations(limit=2, offset=0)
    assert page.total == 5 and page.has_more
    assert page.items[0].session == "hot"

    ids = [c.id for c in cortex.memory.iter_conversations(page_size=2)]
    assert ids == [f"conv-{i}" for i in range(5)]
    assert [r.url.params["offset"] for r in server.requests[1:]] == ["0", "2", "4"]


def test_get_conversation_and_context(make_client: Callable[..., Cortex]) -> None:
    detail = {**CONVERSATION, "message_count": 1, "messages": [MESSAGE]}
    context = {
        "conversation_id": "conv-1",
        "messages": [
            {k: MESSAGE[k] for k in ("id", "role", "content", "token_count", "created_at")}
        ],
        "token_count": 6,
        "last_activity": NOW,
        "source": "cache",
        "memories": [{**MEMORY, "score": 0.7}],
    }
    server = Server(json_response(detail), json_response(context))
    cortex = make_client(server)

    got = cortex.memory.get_conversation("conv-1", message_limit=10)
    assert got.messages[0].content == "Where is my refund?"
    assert server.last.url.params["message_limit"] == "10"

    ctx = cortex.memory.get_context("conv-1", limit=20, query="refund", memory_limit=3)
    assert ctx.source == "cache"
    assert ctx.memories[0].score == 0.7
    assert server.last.url.path == "/v1/conversations/conv-1/context"
    assert dict(server.last.url.params) == {"limit": "20", "query": "refund", "memory_limit": "3"}


def test_ids_are_path_escaped(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "Conversation not found"}, 404))
    with pytest.raises(NotFoundError):
        make_client(server).memory.get_conversation("a/../b")
    assert server.last.url.raw_path == b"/v1/conversations/a%2F..%2Fb"


def test_delete_conversation(make_client: Callable[..., Cortex]) -> None:
    server = Server(httpx.Response(204))
    make_client(server).memory.delete_conversation("conv-1")
    assert server.last.method == "DELETE"


def test_store_and_search_memories(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response(MEMORY, 201),
        json_response({"query": "email", "memories": [{**MEMORY, "score": 0.9}]}),
    )
    cortex = make_client(server)

    memory = cortex.memory.store_memory(
        type="preference", content="The customer prefers email follow-ups.", importance=0.8
    )
    assert memory.summary == "Prefers email"
    assert server.body() == {
        "type": "preference",
        "content": "The customer prefers email follow-ups.",
        "importance": 0.8,
    }

    found = cortex.memory.search_memories(
        query="email", types=["preference", "semantic"], min_importance=0.5, limit=5
    )
    assert found.memories[0].score == 0.9
    assert server.last.url.params.get_list("type") == ["preference", "semantic"]
    assert server.last.url.params["min_importance"] == "0.5"


def test_store_memory_validates_importance(make_client: Callable[..., Cortex]) -> None:
    with pytest.raises(ValidationError, match="importance"):
        make_client(Server(json_response(MEMORY))).memory.store_memory(
            type="semantic", content="x", importance=2
        )


async def test_async_memory(make_async_client: Callable[..., AsyncCortex]) -> None:
    def pages(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return json_response(CONVERSATION, 201)
        offset = int(request.url.params["offset"])
        items = summaries(offset, 1)
        return json_response({"items": items, "total": 2, "limit": 1, "offset": offset})

    cortex = make_async_client(Server(pages))
    assert (await cortex.memory.create_conversation(title="x")).id == "conv-1"
    ids = [c.id async for c in cortex.memory.iter_conversations(page_size=1)]
    assert ids == ["conv-0", "conv-1"]
