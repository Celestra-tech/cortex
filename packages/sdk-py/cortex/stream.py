"""Server-Sent Events and streamed completions.

`chat.stream()` returns a `ChatStream` (or `AsyncChatStream`) that yields
`StreamStart`, then `StreamToken`s, then one `StreamComplete`, or a final
`StreamError` if anything fails. Nothing is sent until iteration begins, and
a stream can be consumed once.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self

import httpx

from ._validation import parse_response
from .errors import CortexError, NetworkError, error_from_response
from .models import Completion

# --- SSE -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServerSentEvent:
    event: str
    data: str
    id: str | None = None


class SSEDecoder:
    """Line-by-line `text/event-stream` decoder per the WHATWG spec.

    Feed it lines without terminators (httpx's `iter_lines` splits on CR, LF,
    and CRLF). An unterminated final event is incomplete and is discarded.
    """

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []
        self._last_id: str | None = None

    def feed(self, line: str) -> ServerSentEvent | None:
        if not line:
            if not self._data:
                self._event = ""
                return None
            message = ServerSentEvent(
                event=self._event or "message", data="\n".join(self._data), id=self._last_id
            )
            self._event, self._data = "", []
            return message
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        elif name == "id" and "\0" not in value:
            self._last_id = value
        return None


def iter_sse(lines: Iterator[str]) -> Iterator[ServerSentEvent]:
    decoder = SSEDecoder()
    for line in lines:
        if (message := decoder.feed(line)) is not None:
            yield message


async def aiter_sse(lines: AsyncIterator[str]) -> AsyncIterator[ServerSentEvent]:
    decoder = SSEDecoder()
    async for line in lines:
        if (message := decoder.feed(line)) is not None:
            yield message


# --- Events --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamStart:
    id: str
    """The completion ID, known before the first token."""
    created_at: str
    provider: str
    model: str
    routing_reason: str
    type: Literal["start"] = "start"


@dataclass(frozen=True, slots=True)
class StreamToken:
    index: int
    delta: str
    type: Literal["token"] = "token"


@dataclass(frozen=True, slots=True)
class StreamComplete:
    completion: Completion
    type: Literal["complete"] = "complete"


@dataclass(frozen=True, slots=True)
class StreamError:
    error: CortexError
    type: Literal["error"] = "error"


ChatStreamEvent = StreamStart | StreamToken | StreamComplete | StreamError


class _StreamState:
    """Event decoding and error normalization shared by both stream flavors."""

    def __init__(self, operation: str, validate: bool) -> None:
        self.operation = operation
        self.validate = validate
        self.consumed = False
        self.completion: Completion | None = None
        self.request_id: str | None = None

    def claim(self) -> None:
        if self.consumed:
            raise CortexError("A chat stream can only be consumed once")
        self.consumed = True

    def opened(self, response: httpx.Response) -> bool:
        """Records the response; False when the server answered with plain JSON."""
        self.request_id = response.headers.get("x-request-id")
        return "text/event-stream" in response.headers.get("content-type", "")

    def fallback(self, body: Any) -> StreamComplete:
        # A server that ignored `stream` answered with the whole completion.
        return self._complete(body)

    def decode(self, message: ServerSentEvent, headers: httpx.Headers) -> ChatStreamEvent | None:
        try:
            data = json.loads(message.data)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        match message.event:
            case "start":
                return StreamStart(
                    id=str(data.get("id", "")),
                    created_at=str(data.get("created_at", "")),
                    provider=str(data.get("provider", "")),
                    model=str(data.get("model", "")),
                    routing_reason=str(data.get("routing_reason") or ""),
                )
            case "token":
                return StreamToken(
                    index=int(data.get("index", 0)), delta=str(data.get("delta", ""))
                )
            case "complete":
                return self._complete(data)
            case "error":
                status = data.get("status")
                return StreamError(
                    error_from_response(
                        status if isinstance(status, int) else 502,
                        data,
                        headers,
                        self.request_id,
                        self.operation,
                    )
                )
            case _:
                return None

    def _complete(self, body: Any) -> StreamComplete:
        self.completion = parse_response(
            self.operation, Completion, body, request_id=self.request_id, strict=self.validate
        )
        return StreamComplete(self.completion)

    def truncated(self) -> StreamError:
        return StreamError(
            NetworkError(
                f"{self.operation} stream ended before completing", request_id=self.request_id
            )
        )

    def failure(self, exc: Exception) -> StreamError:
        if isinstance(exc, CortexError):
            return StreamError(exc)
        return StreamError(
            NetworkError(f"{self.operation} stream failed: {exc!r}", request_id=self.request_id)
        )

    def final(self) -> Completion:
        if self.completion is None:
            raise NetworkError(f"{self.operation} ended without a completion")
        return self.completion


class ChatStream:
    """A streamed completion. Iterate for events, or use `text_stream()` /
    `final_completion()`, which raise the stream's error instead of yielding it.

    Use as a context manager (or call `close()`) to release the connection
    if you stop early.
    """

    def __init__(
        self, open: Callable[[], httpx.Response], *, operation: str, validate: bool
    ) -> None:
        self._open = open
        self._state = _StreamState(operation, validate)
        self._response: httpx.Response | None = None

    @property
    def completion(self) -> Completion | None:
        """The finished completion, once the `complete` event has arrived."""
        return self._state.completion

    @property
    def request_id(self) -> str | None:
        return self._state.request_id

    def __iter__(self) -> Iterator[ChatStreamEvent]:
        self._state.claim()
        return self._events()

    def _events(self) -> Iterator[ChatStreamEvent]:
        state = self._state
        try:
            self._response = response = self._open()
            if not state.opened(response):
                response.read()
                yield state.fallback(response.json())
                return
            for message in iter_sse(response.iter_lines()):
                event = state.decode(message, response.headers)
                if event is None:
                    continue
                yield event
                if isinstance(event, StreamComplete | StreamError):
                    return
            yield state.truncated()
        except (CortexError, httpx.HTTPError, ValueError) as exc:
            yield state.failure(exc)
        finally:
            self.close()

    def text_stream(self) -> Iterator[str]:
        """Just the text deltas."""
        for event in self:
            if isinstance(event, StreamToken):
                yield event.delta
            elif isinstance(event, StreamError):
                raise event.error

    def final_completion(self) -> Completion:
        """Consumes the stream and returns the finished completion."""
        for event in self:
            if isinstance(event, StreamError):
                raise event.error
        return self._state.final()

    def close(self) -> None:
        if self._response is not None:
            self._response.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncChatStream:
    """The `AsyncCortex` flavor of `ChatStream`: `async for event in stream`."""

    def __init__(
        self, open: Callable[[], Awaitable[httpx.Response]], *, operation: str, validate: bool
    ) -> None:
        self._open = open
        self._state = _StreamState(operation, validate)
        self._response: httpx.Response | None = None

    @property
    def completion(self) -> Completion | None:
        return self._state.completion

    @property
    def request_id(self) -> str | None:
        return self._state.request_id

    def __aiter__(self) -> AsyncIterator[ChatStreamEvent]:
        self._state.claim()
        return self._events()

    async def _events(self) -> AsyncIterator[ChatStreamEvent]:
        state = self._state
        try:
            self._response = response = await self._open()
            if not state.opened(response):
                await response.aread()
                yield state.fallback(response.json())
                return
            async for message in aiter_sse(response.aiter_lines()):
                event = state.decode(message, response.headers)
                if event is None:
                    continue
                yield event
                if isinstance(event, StreamComplete | StreamError):
                    return
            yield state.truncated()
        except (CortexError, httpx.HTTPError, ValueError) as exc:
            yield state.failure(exc)
        finally:
            await self.aclose()

    async def text_stream(self) -> AsyncIterator[str]:
        async for event in self:
            if isinstance(event, StreamToken):
                yield event.delta
            elif isinstance(event, StreamError):
                raise event.error

    async def final_completion(self) -> Completion:
        async for event in self:
            if isinstance(event, StreamError):
                raise event.error
        return self._state.final()

    async def aclose(self) -> None:
        if self._response is not None:
            await self._response.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
