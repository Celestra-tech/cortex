import json
from typing import Any

import httpx2
import pytest

from cortex_api.services.router.base import ProviderErrorKind, ProviderName

from .fakes import FakeProvider

pytestmark = [pytest.mark.database, pytest.mark.redis]

HELLO = {"messages": [{"role": "user", "content": "Say hello."}]}


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_streams_start_tokens_then_the_full_completion(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.post(
        "/v1/chat/completions",
        json={**HELLO, "model": "claude-haiku-4-5", "stream": True},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "start" and names[-1] == "complete"
    assert set(names[1:-1]) == {"token"}

    start, complete = events[0][1], events[-1][1]
    assert start["id"] == complete["id"]
    assert (start["provider"], start["model"]) == ("anthropic", "claude-haiku-4-5")
    tokens = [data for name, data in events if name == "token"]
    assert [t["index"] for t in tokens] == list(range(len(tokens)))
    assert "".join(t["delta"] for t in tokens) == complete["output"]
    assert complete["object"] == "cortex.completion"


async def test_non_streaming_responses_are_unchanged(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.post("/v1/chat/completions", json=HELLO, headers=headers)
    assert response.headers["content-type"] == "application/json"
    assert response.json()["object"] == "cortex.completion"


async def test_failures_before_the_stream_are_http_errors(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    providers: dict[ProviderName, FakeProvider],
) -> None:
    for provider in providers.values():
        provider.fail(ProviderErrorKind.BAD_REQUEST, message="nope")
    response = await api.post(
        "/v1/chat/completions", json={**HELLO, "stream": True}, headers=headers
    )
    assert response.status_code == 502
    assert response.json()["detail"].startswith("All providers failed")
