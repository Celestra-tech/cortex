from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from cortex import (
    AsyncCortex,
    Cortex,
    ResponseValidationError,
    ValidationError,
)
from cortex.models import Completion

from .helpers import HELLO, Server, completion, json_response

GROUNDED = completion(
    conversation_id="conv-1",
    knowledge={
        "query_id": "q1",
        "applied": True,
        "confidence": {"score": 0.82, "level": "high"},
        "citations": [
            {
                "index": 1,
                "document_id": "d1",
                "title": "Refund policy",
                "label": "Refund policy > Timing",
                "score": 0.9,
                "snippet": "Refunds post within 5 days.",
                "cited": True,
            }
        ],
        "cited": [1],
    },
)


def test_complete_sends_request_and_parses_completion(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(GROUNDED))
    result = make_client(server).chat.complete(
        messages=HELLO,
        model="openai/gpt-5-mini",
        objective="quality",
        temperature=0.2,
        max_tokens=256,
        memory={"conversation_id": "conv-1", "memory_limit": 3},
        knowledge={"top_k": 4, "min_confidence": 0.3},
        metadata={"feature": "support"},
        routing={"mode": "preferred", "allow_fallback": True},
    )

    assert isinstance(result, Completion)
    assert result.output == "Hello there!"
    assert result.created_at == datetime(2026, 9, 27, 12, tzinfo=UTC)
    assert result.knowledge is not None
    assert result.knowledge.citations[0].label == "Refund policy > Timing"
    assert result.knowledge.cited == [1]

    request = server.last
    assert request.method == "POST"
    assert request.url.path == "/v1/chat/completions"
    assert server.body() == {
        "messages": HELLO,
        "model": "openai/gpt-5-mini",
        "objective": "quality",
        "temperature": 0.2,
        "max_tokens": 256,
        "memory": {"conversation_id": "conv-1", "memory_limit": 3},
        "knowledge": {"top_k": 4, "min_confidence": 0.3},
        "metadata": {"feature": "support"},
        "routing": {"mode": "preferred", "allow_fallback": True},
        "stream": False,
    }


def test_omitted_options_are_not_sent(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(completion()))
    make_client(server).chat.complete(messages=HELLO)
    assert server.body() == {"messages": HELLO, "stream": False}


def test_knowledge_filter_datetimes_are_serialized(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(completion()))
    after = datetime(2026, 1, 1, tzinfo=UTC)
    make_client(server).chat.complete(
        messages=HELLO, knowledge={"filters": {"created_after": after, "sources": ["wiki"]}}
    )
    assert server.body()["knowledge"]["filters"] == {
        "created_after": "2026-01-01T00:00:00Z",
        "sources": ["wiki"],
    }


@pytest.mark.parametrize(
    ("kwargs", "path"),
    [
        ({"messages": []}, "messages"),
        ({"messages": [{"role": "user", "content": ""}]}, "messages.0.content"),
        ({"messages": [{"role": "robot", "content": "hi"}]}, "messages.0.role"),
        ({"messages": HELLO, "temperature": 3}, "temperature"),
        ({"messages": HELLO, "memory": {"memory_limit": 2}}, "memory.conversation_id"),
        ({"messages": HELLO, "routing": {"provider": "acme"}}, "routing.provider"),
        ({"messages": [{"role": "system", "content": "Be nice"}]}, "messages"),
    ],
)
def test_invalid_input_fails_before_sending(
    make_client: Callable[..., Cortex], kwargs: dict[str, object], path: str
) -> None:
    server = Server(json_response(completion()))
    with pytest.raises(ValidationError) as caught:
        make_client(server).chat.complete(**kwargs)  # type: ignore[arg-type]
    assert caught.value.status is None
    assert path in [issue.path for issue in caught.value.issues]
    assert str(caught.value).startswith("chat.complete: invalid input")
    assert server.requests == []


def test_server_validation_errors_carry_issues(make_client: Callable[..., Cortex]) -> None:
    detail = [{"loc": ["body", "messages", 0, "content"], "msg": "too long", "type": "x"}]
    server = Server(json_response({"detail": detail}, 422))
    with pytest.raises(ValidationError) as caught:
        make_client(server).chat.complete(messages=HELLO)
    assert caught.value.status == 422
    assert caught.value.issues[0].path == "messages.0.content"
    assert "messages.0.content: too long" in str(caught.value)


def test_unexpected_response_shape(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"id": "c1", "output": 42}, headers={"x-request-id": "srv-2"}))
    with pytest.raises(ResponseValidationError) as caught:
        make_client(server).chat.complete(messages=HELLO)
    assert "srv-2" in str(caught.value)
    assert caught.value.body == {"id": "c1", "output": 42}


def test_lenient_mode_degrades_instead_of_raising(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"id": "c1", "output": "hi"}))
    result = make_client(server, validate_responses=False).chat.complete(messages=HELLO)
    assert result.output == "hi"


def test_additive_api_changes_are_tolerated(make_client: Callable[..., Cortex]) -> None:
    body = completion(finish_reason="brand_new_reason", provider="mistral", shiny_new_field=1)
    result = make_client(Server(json_response(body))).chat.complete(messages=HELLO)
    assert result.finish_reason == "brand_new_reason"
    assert result.model_extra == {"shiny_new_field": 1}


async def test_async_complete(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(completion()))
    result = await make_async_client(server).chat.complete(messages=HELLO)
    assert result.tokens.total == 8
    assert server.body()["stream"] is False
