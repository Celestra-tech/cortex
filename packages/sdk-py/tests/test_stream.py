from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from cortex import (
    AsyncCortex,
    AuthenticationError,
    Cortex,
    CortexError,
    NetworkError,
    ProviderError,
    StreamComplete,
    StreamError,
    StreamStart,
    StreamToken,
)
from cortex.stream import SSEDecoder, iter_sse

from .helpers import HELLO, Server, completion, json_response, sse

START = {
    "id": "c1",
    "created_at": "2026-09-27T12:00:00Z",
    "provider": "openai",
    "model": "gpt-5-mini",
    "routing_reason": "balanced objective",
}


def happy_stream() -> httpx.Response:
    return sse(
        ("start", START),
        ("token", {"index": 0, "delta": "Hello "}),
        ("token", {"index": 1, "delta": "there!"}),
        ("complete", completion(id="c1")),
        headers={"x-request-id": "srv-9"},
    )


# --- SSE parsing ---------------------------------------------------------------------------------


def test_sse_decoder_handles_the_spec_edge_cases() -> None:
    lines = [
        ": comment",
        "event: token",
        "data: first",
        "data:second",
        "id: 7",
        "",
        "",
        "data: default event",
        "",
        "event: unterminated",
        "data: dropped",
    ]
    events = list(iter_sse(iter(lines)))
    assert [(e.event, e.data, e.id) for e in events] == [
        ("token", "first\nsecond", "7"),
        ("message", "default event", "7"),
    ]


def test_sse_decoder_ignores_ids_with_nul() -> None:
    decoder = SSEDecoder()
    decoder.feed("id: a\0b")
    decoder.feed("data: x")
    event = decoder.feed("")
    assert event is not None and event.id is None


def test_stream_parses_crlf_bodies(make_client: Callable[..., Cortex]) -> None:
    body = (
        f"event: start\r\ndata: {json.dumps(START)}\r\n\r\n"
        f'event: token\r\ndata: {{"index": 0, "delta": "Hi"}}\r\n\r\n'
        f"event: complete\r\ndata: {json.dumps(completion(output='Hi'))}\r\n\r\n"
    )
    server = Server(
        httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    )
    stream = make_client(server).chat.stream(messages=HELLO)
    assert "".join(stream.text_stream()) == "Hi"


# --- ChatStream ----------------------------------------------------------------------------------


def test_stream_yields_typed_events(make_client: Callable[..., Cortex]) -> None:
    server = Server(happy_stream())
    stream = make_client(server).chat.stream(messages=HELLO, model="openai/gpt-5-mini")

    events = list(stream)

    assert events[0] == StreamStart(
        id="c1",
        created_at="2026-09-27T12:00:00Z",
        provider="openai",
        model="gpt-5-mini",
        routing_reason="balanced objective",
    )
    assert events[1:3] == [StreamToken(0, "Hello "), StreamToken(1, "there!")]
    assert isinstance(events[3], StreamComplete)
    assert events[3].completion.id == "c1"
    assert stream.completion is not None and stream.completion.tokens.total == 8
    assert stream.request_id == "srv-9"

    request = server.last
    assert request.headers["accept"] == "text/event-stream"
    assert json.loads(request.content)["stream"] is True
    assert json.loads(request.content)["model"] == "openai/gpt-5-mini"


def test_stream_supports_pattern_matching(make_client: Callable[..., Cortex]) -> None:
    text = []
    for event in make_client(Server(happy_stream())).chat.stream(messages=HELLO):
        match event:
            case StreamToken(delta=delta):
                text.append(delta)
            case StreamComplete(completion=done):
                assert done.output == "Hello there!"
    assert "".join(text) == "Hello there!"


def test_stream_is_lazy_and_single_use(make_client: Callable[..., Cortex]) -> None:
    server = Server(happy_stream())
    stream = make_client(server).chat.stream(messages=HELLO)
    assert server.requests == []
    assert stream.final_completion().output == "Hello there!"
    assert len(server.requests) == 1
    with pytest.raises(CortexError, match="consumed once"):
        list(stream)


def test_stream_validates_input_eagerly(make_client: Callable[..., Cortex]) -> None:
    with pytest.raises(CortexError, match="messages"):
        make_client(Server(happy_stream())).chat.stream(messages=[])


def test_http_error_before_stream_becomes_error_event(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "Invalid API key"}, 401))
    events = list(make_client(server).chat.stream(messages=HELLO))
    assert len(events) == 1
    assert isinstance(events[0], StreamError)
    assert isinstance(events[0].error, AuthenticationError)


def test_helpers_raise_the_stream_error(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "Invalid API key"}, 401))
    with pytest.raises(AuthenticationError):
        make_client(server).chat.stream(messages=HELLO).final_completion()
    with pytest.raises(AuthenticationError):
        list(make_client(server).chat.stream(messages=HELLO).text_stream())


def test_error_event_mid_stream(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        sse(
            ("start", START),
            ("token", {"index": 0, "delta": "Hel"}),
            ("error", {"status": 502, "detail": "provider dropped"}),
            ("token", {"index": 1, "delta": "never"}),
        )
    )
    events = list(make_client(server).chat.stream(messages=HELLO))
    assert [e.type for e in events] == ["start", "token", "error"]
    last = events[-1]
    assert isinstance(last, StreamError)
    assert isinstance(last.error, ProviderError)
    assert "provider dropped" in str(last.error)


def test_truncated_stream_is_a_network_error(make_client: Callable[..., Cortex]) -> None:
    server = Server(sse(("start", START), ("token", {"index": 0, "delta": "Hel"})))
    stream = make_client(server).chat.stream(messages=HELLO)
    with pytest.raises(NetworkError, match="ended before completing"):
        stream.final_completion()


def test_unknown_events_and_bad_json_are_skipped(make_client: Callable[..., Cortex]) -> None:
    body = (
        "event: ping\ndata: {}\n\n"
        "event: token\ndata: not json\n\n"
        f"event: complete\ndata: {json.dumps(completion())}\n\n"
    )
    server = Server(
        httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
    )
    events = list(make_client(server).chat.stream(messages=HELLO))
    assert [e.type for e in events] == ["complete"]


def test_json_fallback_when_server_does_not_stream(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(completion(output="whole answer")))
    events = list(make_client(server).chat.stream(messages=HELLO))
    assert len(events) == 1
    assert isinstance(events[0], StreamComplete)
    assert events[0].completion.output == "whole answer"


def test_stream_retries_before_it_opens(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({}, 503), happy_stream())
    assert make_client(server).chat.stream(messages=HELLO).final_completion().id == "c1"
    assert len(server.requests) == 2


def test_breaking_early_closes_the_response(make_client: Callable[..., Cortex]) -> None:
    stream = make_client(Server(happy_stream())).chat.stream(messages=HELLO)
    with stream:
        for event in stream:
            if isinstance(event, StreamToken):
                break
    assert stream._response is not None and stream._response.is_closed


async def test_async_stream(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(happy_stream())
    stream = make_async_client(server).chat.stream(messages=HELLO)
    types = [event.type async for event in stream]
    assert types == ["start", "token", "token", "complete"]
    assert stream.completion is not None


async def test_async_stream_helpers(make_async_client: Callable[..., AsyncCortex]) -> None:
    cortex = make_async_client(Server(happy_stream()))
    assert "".join([d async for d in cortex.chat.stream(messages=HELLO).text_stream()]) == (
        "Hello there!"
    )
    done = await cortex.chat.stream(messages=HELLO).final_completion()
    assert done.output == "Hello there!"

    failing = make_async_client(Server(json_response({"detail": "x"}, 401)))
    with pytest.raises(AuthenticationError):
        await failing.chat.stream(messages=HELLO).final_completion()
