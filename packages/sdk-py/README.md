# celestra-cortex

Python SDK for CELESTRA Cortex: routed chat, conversation memory, knowledge
retrieval, and document ingestion behind one typed client, with
authentication, retries, streaming, and middleware built in. Sync (`Cortex`)
and asyncio (`AsyncCortex`) clients share the same API.

- [Quick start](#quick-start)
- [Installation](#installation)
- [Authentication](#authentication)
- [Chat](#chat)
- [Streaming](#streaming)
- [Memory](#memory)
- [Knowledge](#knowledge)
- [Documents](#documents)
- [Evidence](#evidence)
- [Scenarios](#scenarios)
- [Errors](#errors)
- [Retries, timeouts, and request IDs](#retries-timeouts-and-request-ids)
- [Middleware](#middleware)

## Quick start

```python
import os

from cortex import Cortex

cortex = Cortex(api_key=os.environ["CORTEX_API_KEY"], base_url="http://localhost:8000")

completion = cortex.chat.complete(
    messages=[{"role": "user", "content": "Give me three names for a coffee shop."}],
)
print(completion.output)
print(f"{completion.provider}/{completion.model}", completion.routing_reason)
```

Async:

```python
import asyncio

from cortex import AsyncCortex


async def main() -> None:
    async with AsyncCortex() as cortex:  # reads CORTEX_API_KEY and CORTEX_BASE_URL
        completion = await cortex.chat.complete(
            messages=[{"role": "user", "content": "Hello"}],
        )
        print(completion.output)


asyncio.run(main())
```

Responses are Pydantic v2 models, and request parameters are `TypedDict`s,
so plain dicts work and your type checker catches typos.

## Installation

Requires Python 3.13. Dependencies: `httpx` and `pydantic`.

Inside this repository the package is a uv workspace member:

```bash
uv sync --all-packages      # from the repository root
uv run python -c "import cortex; print(cortex.__version__)"
```

From another project, install it from a checkout:

```bash
uv add ./path/to/cortex/packages/sdk-py
# or: pip install ./path/to/cortex/packages/sdk-py
```

## Authentication

Every `/v1` call carries an API key as `Authorization: Bearer ctx_...`. The
key identifies its organization, so nothing else is required.

Mint a key with the API's CLI (the secret prints once, to stdout):

```bash
cd apps/api
uv run python -m cortex_api.cli create-organization --name "Acme" --slug acme
uv run python -m cortex_api.cli create-api-key --organization acme --name "backend"
# ctx_4yJ0...   <- store this in your secret manager
uv run python -m cortex_api.cli revoke-api-key --organization acme <key id>
```

```python
cortex = Cortex(
    api_key=os.environ["CORTEX_API_KEY"],  # or a callable, called before every attempt
    organization_id="0198f7a2-...",  # optional; must match the key's organization
)
```

| Argument          | Default                                         | Notes                                                              |
| ----------------- | ----------------------------------------------- | ------------------------------------------------------------------ |
| `api_key`         | `CORTEX_API_KEY`                                | `str` or `Callable[[], str]` for rotating keys. `None` sends none. |
| `organization_id` | `CORTEX_ORGANIZATION_ID`                        | Sent as `X-Organization-ID`. A mismatch with the key is a `403`.   |
| `base_url`        | `CORTEX_BASE_URL`, then `http://localhost:8000` |                                                                    |

Without a key, development servers accept `X-Organization-ID` alone. Servers
started with `CORTEX_AUTH_REQUIRE_API_KEY=true` reject that with `401`.

Clients hold a connection pool: reuse one per process, and close it (or use
`with Cortex() as cortex:` / `async with AsyncCortex() as cortex:`) when done.

## Chat

```python
completion = cortex.chat.complete(
    model="anthropic/claude-sonnet-4-5",  # or omit and set an objective
    objective="quality",  # balanced | quality | speed | cost
    messages=[
        {"role": "system", "content": "You are a concise support agent."},
        {"role": "user", "content": "How do I reset my password?"},
    ],
    temperature=0.3,
    max_tokens=400,
    routing={"mode": "preferred", "allow_fallback": True},
    metadata={"feature": "help-center"},
)

completion.tokens.total
completion.cost_estimate  # USD
completion.attempts  # every provider call, including failed fallbacks
```

List what can be routed to:

```python
for model in cortex.router.models().models:
    if model.available and model.allowed:
        print(model.id, model.capabilities, model.pricing.output_per_mtok)
```

Grounded answers with numbered citations come from `knowledge`;
conversational memory comes from `memory` (see below). Both work with
streaming too.

```python
answer = cortex.chat.complete(
    messages=[{"role": "user", "content": "How long do refunds take?"}],
    knowledge={"top_k": 5, "min_confidence": 0.3},
)
if answer.knowledge:
    for citation in answer.knowledge.citations:
        print(f"[{citation.index}] {citation.label}")
```

## Streaming

```python
from cortex import StreamComplete, StreamError, StreamStart, StreamToken

with cortex.chat.stream(messages=[{"role": "user", "content": "Write a haiku."}]) as stream:
    for event in stream:
        match event:
            case StreamStart(provider=provider, model=model):
                print(f"routing to {provider}/{model}")
            case StreamToken(delta=delta):
                print(delta, end="", flush=True)
            case StreamComplete(completion=completion):
                print("\n", completion.tokens)
            case StreamError(error=error):
                print("failed:", error)  # a typed CortexError
```

Or skip the event plumbing:

```python
for text in cortex.chat.stream(messages=messages).text_stream():
    print(text, end="")

completion = cortex.chat.stream(messages=messages).final_completion()
```

With `AsyncCortex`, use `async for event in cortex.chat.stream(...)`,
`async for text in stream.text_stream()`, and `await stream.final_completion()`.

- Parameters are validated immediately; the request is sent when iteration
  begins, and a stream can be consumed once.
- Failures, including HTTP errors before the stream opens, arrive as a final
  `StreamError`. `text_stream()` and `final_completion()` raise them instead.
- Leaving the `with` block (or breaking out of the loop) closes the connection.
- The server currently runs the provider call to completion and then streams
  the output as token events, so time to first token equals the full
  latency. Native provider streaming is planned; the event protocol will not
  change.

## Memory

Conversations keep a hot Redis session backed by Postgres; long-term memories
are ranked by relevance, importance, and recency.

```python
conversation = cortex.memory.create_conversation(title="Support")

cortex.memory.add_message(
    conversation_id=conversation.id,
    role="user",
    content="I prefer email over phone calls.",
)

# Let the router prepend history and relevant memories, then persist the turn.
reply = cortex.chat.complete(
    messages=[{"role": "user", "content": "How should you contact me?"}],
    memory={"conversation_id": conversation.id},
)

context = cortex.memory.get_context(conversation.id, memory_limit=5)
cortex.memory.store_memory(
    type="preference", content="Customer prefers email follow-ups.", importance=0.8
)
found = cortex.memory.search_memories(query="contact preference", types=["preference"])

for summary in cortex.memory.iter_conversations():
    print(summary.id, summary.message_count, summary.session)
```

## Knowledge

```python
result = cortex.knowledge.search(
    "How much paid leave do we get?",
    top_k=8,
    mode="hybrid",
    filters={"sources": ["handbook.pdf"]},
)

result.context.text  # numbered sources, ready for a system prompt
result.context.citations  # what each [n] refers to
result.context.confidence  # score and level: high | medium | low | none
result.results  # ranked chunks with vector and keyword scores
```

`cortex.knowledge.metrics()`, `list_queries()`, `iter_queries()`, and
`get_query(id)` expose retrieval quality and the query log.

## Documents

```python
doc = cortex.documents.upload(
    "handbook.pdf",  # a path, bytes (with filename=), or a binary file object
    metadata={"team": "people"},
    chunking={"chunk_size": 400, "chunk_overlap": 40},
)
print(doc.chunk_count, doc.ingestion.ingestion_ms)

cortex.documents.create(title="FAQ", content="# FAQ\n...", mime_type="text/markdown")
page = cortex.documents.list(limit=20)
detail = cortex.documents.get(doc.id, include_chunks=True)
cortex.documents.delete(doc.id)
```

PDF, DOCX, Markdown, and plain text are supported; the format is detected
from the file name. Uploading identical content twice raises `ConflictError`,
whose `document_id` is the existing copy. Ingestion calls default to a 120 s
timeout.

## Evidence

Every completion is recorded as a decision linked to the evidence behind it.
Look it up by the completion id:

```python
evidence = cortex.evidence.decision(completion.id)
for item in evidence.supporting:
    print(item.node.type, item.node.title, item.strength)

graph = cortex.evidence.graph(completion.id, depth=3)
graph.edges[0].provenance  # confidence, explanation, source, timestamp
node = cortex.evidence.node(graph.nodes[1].id)  # neighbors + decisions it fed
path = cortex.evidence.path(node.node.id, evidence.decision.id)

cortex.evidence.record_decision(
    title="Approve refund for order 1042",
    source="policy-engine@3",
    evidence=[
        {
            "type": "document",
            "ref_id": doc_id,
            "title": "Refund policy",
            "explanation": "Inside the 30-day window",
        },
    ],
)
```

`list_decisions()` and `iter_decisions()` page through decisions, newest
first. `record_decision` is not retried; a repeat `ref_id` raises
`ConflictError`.

## Scenarios

Plan how to act on a decision. The simulator returns best case, base case,
worst case, aggressive, and conservative scenarios, ranked, each with the
stance it took, the assumptions it made, and the outcomes that follow:

```python
simulation = cortex.scenarios.simulate(
    completion.id,
    objective="Keep the customer without losing money",
    constraints=[{"statement": "Refund stays under $500", "severity": "hard"}],
    assumptions=[{"statement": "Finance approves the budget", "confidence": 0.8}],
    risk_tolerance=0.4,  # 0 avoids harm, 1 chases the objective
)
best = simulation.recommended
print(best.name, best.score, best.criteria["risk_exposure"].value)
for assumption in best.assumptions:
    print(assumption.kind, assumption.statement, assumption.evidence_node_id)

history = cortex.scenarios.for_decision(completion.id)  # newest first
scenario = cortex.scenarios.get(best.id)
```

`list()` and `iter()` page through every simulation. `simulate` is not
retried, since each call records a new simulation.

## Errors

Every error extends `CortexError`. HTTP failures extend `APIError`, which
carries `status`, `body`, `detail`, `headers`, and `request_id`.

| Class                     | When                                                                                                                   |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `AuthenticationError`     | 401: missing, invalid, or revoked key.                                                                                 |
| `PermissionDeniedError`   | 403: organization mismatch or routing policy.                                                                          |
| `NotFoundError`           | 404.                                                                                                                   |
| `ConflictError`           | 409: duplicate document (`document_id`).                                                                               |
| `ValidationError`         | 400/413/415/422 from the server (`issues` per field), or `status=None` when the SDK rejected the input before sending. |
| `RateLimitError`          | 429 after retries (`retry_after`, seconds).                                                                            |
| `ProviderError`           | 502/503: no provider could serve it (`attempts`, `completion_id`).                                                     |
| `InternalServerError`     | Other 5xx.                                                                                                             |
| `NetworkError`            | No response. Subclass: `RequestTimeoutError`.                                                                          |
| `ResponseValidationError` | A 2xx whose body is not the documented shape.                                                                          |

```python
from cortex import ProviderError, RateLimitError, ValidationError

try:
    cortex.chat.complete(messages=messages)
except ValidationError as error:
    for issue in error.issues:
        print(issue.path, issue.message)
except RateLimitError as error:
    print(f"retry in {error.retry_after}s")
except ProviderError as error:
    print(error.attempts)
```

Responses tolerate additive API changes: unknown fields land in
`model.model_extra`, and enum-like fields are plain strings. Pass
`validate_responses=False` to get a best-effort model instead of
`ResponseValidationError` when a response does not match.

## Retries, timeouts, and request IDs

- Up to `max_retries` (default 2) retries with exponential backoff and jitter,
  honoring `Retry-After`. Configure with
  `retry=RetryConfig(max_retries=3, initial_delay=0.5, max_delay=8)`.
- Every call retries on 429, 503, and failures to connect. Only calls that are
  safe to repeat (GET, DELETE, and `knowledge.search`) also retry after
  timeouts, dropped connections, and 408/500/502/504, so a completion is never
  billed twice.
- `timeout` (default 60 s) applies per attempt.
- Each call gets an `X-Request-ID` (`req_<hex>`), reused across its retries and
  echoed by the server. Errors expose it as `request_id`; quote it in bug reports.

Every method accepts per-call overrides:

```python
cortex.router.models(
    request_options={
        "timeout": 5,
        "max_retries": 0,
        "request_id": "job-42",
        "headers": {"X-A": "1"},
    }
)
fast = cortex.with_options(timeout=5, max_retries=0)  # shares the connection pool
```

## Middleware

Middleware wraps every attempt (outermost first) and may read or mutate the
`httpx.Request`, including headers. For `Cortex` it is a function; for
`AsyncCortex`, an async function. The built-ins work with both.

```python
import logging
import time

import httpx

from cortex import (
    Cortex,
    HeadersMiddleware,
    LoggingMiddleware,
    RequestContext,
    TelemetryMiddleware,
    TracingMiddleware,
)
from cortex.middleware import Next


def timing(request: httpx.Request, context: RequestContext, call_next: Next) -> httpx.Response:
    started = time.perf_counter()
    response = call_next(request)
    print(context.operation, context.attempt, response.status_code, time.perf_counter() - started)
    return response


cortex = Cortex(
    middleware=[
        LoggingMiddleware(logging.getLogger("cortex")),  # one line per attempt, no bodies
        TelemetryMiddleware(lambda e: metrics.histogram("cortex.seconds", e.duration)),
        HeadersMiddleware({"X-App": "billing"}),
        TracingMiddleware(),  # W3C traceparent
        timing,
    ],
)
cortex.use(HeadersMiddleware(lambda request, context: {"X-Attempt": str(context.attempt)}))
```

Subclass `cortex.Middleware` and override `before` / `after` (or `handle` and
`ahandle`) to write middleware for both clients. Without an active trace,
`TracingMiddleware` derives the trace ID from the request ID, so client and
server logs join on either value; pass `current=` to propagate an existing
trace (for example from OpenTelemetry).
