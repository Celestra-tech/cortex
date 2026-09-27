from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from cortex.models import ChatMessageParam

BASE_URL = "https://cortex.test"
API_KEY = "ctx_test_key"
ORG = "0198f7a2-0000-7000-8000-000000000001"

Handler = Callable[[httpx.Request], httpx.Response]
Reply = httpx.Response | Exception | Handler


class Server:
    """A scripted `httpx.MockTransport`: replies are consumed in order (the
    last one repeats) and every request is recorded."""

    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        response = reply if isinstance(reply, httpx.Response) else reply(request)
        # A response object can only be sent once, and replies may repeat.
        return httpx.Response(
            response.status_code, headers=response.headers, content=response.content
        )

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, index: int = -1) -> Any:
        return json.loads(self.requests[index].content)


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)

    async def asleep(self, seconds: float) -> None:
        self.append(seconds)


def json_response(
    body: Any, status: int = 200, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def sse(*events: tuple[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
    text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx.Response(
        200,
        content=text.encode(),
        headers={"content-type": "text/event-stream", **(headers or {})},
    )


def completion(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "0198f7a2-1111-7000-8000-000000000001",
        "object": "cortex.completion",
        "created_at": "2026-09-27T12:00:00Z",
        "provider": "openai",
        "model": "gpt-5-mini",
        "output": "Hello there!",
        "latency_ms": 42.5,
        "tokens": {"prompt": 5, "completion": 3, "total": 8},
        "finish_reason": "stop",
        "routing_reason": "balanced objective",
        "routing_mode": "auto",
        "cost_estimate": 0.0001,
        "attempts": [
            {
                "provider": "openai",
                "model": "gpt-5-mini",
                "success": True,
                "latency_ms": 40.0,
                "error": None,
            }
        ],
        "conversation_id": None,
        "knowledge": None,
        **overrides,
    }


HELLO: list[ChatMessageParam] = [{"role": "user", "content": "Hello"}]
