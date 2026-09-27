from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ._resource import AsyncAPIResource, SyncAPIResource
from ._transport import Call, RequestOptions
from ._validation import invalid, validate_params
from .models import (
    ChatMessageParam,
    Completion,
    CompletionParams,
    KnowledgeOptions,
    MemoryOptions,
    Metadata,
    Objective,
    RoutingOptions,
)
from .stream import AsyncChatStream, ChatStream


def _completion_call(
    operation: str,
    *,
    stream: bool,
    request_options: RequestOptions | None,
    **params: Any,
) -> Call:
    body = validate_params(
        operation, CompletionParams, {k: v for k, v in params.items() if v is not None}
    )
    if all(message["role"] == "system" for message in body["messages"]):
        raise invalid(operation, "messages", "must include at least one non-system message")
    return Call(
        operation=operation,
        method="POST",
        path="/v1/chat/completions",
        json={**body, "stream": stream},
        headers={"Accept": "text/event-stream"} if stream else None,
        options=request_options,
    )


class Chat(SyncAPIResource):
    """Routed chat completions, optionally with conversation memory and
    knowledge grounding."""

    def complete(
        self,
        *,
        messages: Sequence[ChatMessageParam],
        model: str | None = None,
        objective: Objective | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        memory: MemoryOptions | None = None,
        knowledge: KnowledgeOptions | None = None,
        metadata: Metadata | None = None,
        routing: RoutingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Completion:
        """Runs a completion. Cortex picks the model unless `model` or
        `routing` says otherwise, and falls back across providers on failure.

        Not retried after timeouts or 5xx, since the provider may already have
        run (and billed) the request; the server retries providers itself.
        """
        call = _completion_call(
            "chat.complete",
            stream=False,
            request_options=request_options,
            messages=list(messages),
            model=model,
            objective=objective,
            temperature=temperature,
            max_tokens=max_tokens,
            memory=memory,
            knowledge=knowledge,
            metadata=metadata,
            routing=routing,
        )
        return self._transport.request(call, Completion)

    def stream(
        self,
        *,
        messages: Sequence[ChatMessageParam],
        model: str | None = None,
        objective: Objective | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        memory: MemoryOptions | None = None,
        knowledge: KnowledgeOptions | None = None,
        metadata: Metadata | None = None,
        routing: RoutingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> ChatStream:
        """Like `complete`, but yields `start`, `token`, and `complete` events.

        Parameters are validated immediately; the request is sent when
        iteration begins.
        """
        call = _completion_call(
            "chat.stream",
            stream=True,
            request_options=request_options,
            messages=list(messages),
            model=model,
            objective=objective,
            temperature=temperature,
            max_tokens=max_tokens,
            memory=memory,
            knowledge=knowledge,
            metadata=metadata,
            routing=routing,
        )
        return ChatStream(
            lambda: self._transport.send(call, stream=True),
            operation=call.operation,
            validate=self._transport.validate_responses,
        )


class AsyncChat(AsyncAPIResource):
    async def complete(
        self,
        *,
        messages: Sequence[ChatMessageParam],
        model: str | None = None,
        objective: Objective | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        memory: MemoryOptions | None = None,
        knowledge: KnowledgeOptions | None = None,
        metadata: Metadata | None = None,
        routing: RoutingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Completion:
        call = _completion_call(
            "chat.complete",
            stream=False,
            request_options=request_options,
            messages=list(messages),
            model=model,
            objective=objective,
            temperature=temperature,
            max_tokens=max_tokens,
            memory=memory,
            knowledge=knowledge,
            metadata=metadata,
            routing=routing,
        )
        return await self._transport.request(call, Completion)

    def stream(
        self,
        *,
        messages: Sequence[ChatMessageParam],
        model: str | None = None,
        objective: Objective | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        memory: MemoryOptions | None = None,
        knowledge: KnowledgeOptions | None = None,
        metadata: Metadata | None = None,
        routing: RoutingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> AsyncChatStream:
        call = _completion_call(
            "chat.stream",
            stream=True,
            request_options=request_options,
            messages=list(messages),
            model=model,
            objective=objective,
            temperature=temperature,
            max_tokens=max_tokens,
            memory=memory,
            knowledge=knowledge,
            metadata=metadata,
            routing=routing,
        )
        return AsyncChatStream(
            lambda: self._transport.send(call, stream=True),
            operation=call.operation,
            validate=self._transport.validate_responses,
        )
