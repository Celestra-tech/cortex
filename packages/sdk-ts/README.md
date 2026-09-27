# @celestra/cortex-sdk

TypeScript SDK for CELESTRA Cortex: routed chat, conversation memory,
knowledge retrieval, and document ingestion behind one typed client, with
authentication, retries, streaming, and middleware built in.

- [Quick start](#quick-start)
- [Installation](#installation)
- [Authentication](#authentication)
- [Chat](#chat)
- [Streaming](#streaming)
- [Memory](#memory)
- [Knowledge](#knowledge)
- [Documents](#documents)
- [Errors](#errors)
- [Retries, timeouts, and request IDs](#retries-timeouts-and-request-ids)
- [Middleware](#middleware)

## Quick start

```ts
import { Cortex } from "@celestra/cortex-sdk";

const cortex = new Cortex({
  apiKey: process.env.CORTEX_API_KEY,
  baseURL: "http://localhost:8000",
});

const completion = await cortex.chat.complete({
  messages: [{ role: "user", content: "Give me three names for a coffee shop." }],
});

console.log(completion.output);
console.log(`${completion.provider}/${completion.model}`, completion.routing_reason);
```

Cortex chooses the model unless you name one, falls back across providers on
failure, and reports tokens, latency, cost, and every provider attempt on the
response.

## Installation

The SDK ships as TypeScript source inside the monorepo; consumers compile it
directly. Add it to a workspace package:

```json
{
  "dependencies": {
    "@celestra/cortex-sdk": "workspace:*"
  }
}
```

Requirements: TypeScript 5, an ESM project, and a runtime with `fetch`
(Node 20+, Bun, Deno, edge runtimes, browsers). The only runtime dependency is
Zod.

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

```ts
const cortex = new Cortex({
  apiKey: process.env.CORTEX_API_KEY, // or a function, called before every attempt
  organizationId: "0198f7a2-...", // optional; must match the key's organization
});
```

| Option           | Default                                         | Notes                                                                             |
| ---------------- | ----------------------------------------------- | --------------------------------------------------------------------------------- |
| `apiKey`         | `CORTEX_API_KEY`                                | String or `() => string \| Promise<string>` for rotating keys. `null` sends none. |
| `organizationId` | `CORTEX_ORGANIZATION_ID`                        | Sent as `X-Organization-ID`. A mismatch with the key is a `403`.                  |
| `baseURL`        | `CORTEX_BASE_URL`, then `http://localhost:8000` |                                                                                   |

Without a key, development servers accept `X-Organization-ID` alone. Servers
started with `CORTEX_AUTH_REQUIRE_API_KEY=true` reject that with `401`.

API keys are secrets: in a browser the client refuses to hold one unless you
pass `dangerouslyAllowBrowser: true`. Call Cortex from your server instead.

## Chat

```ts
const completion = await cortex.chat.complete({
  model: "anthropic/claude-sonnet-4-5", // or omit and set an objective
  objective: "quality", // balanced | quality | speed | cost
  messages: [
    { role: "system", content: "You are a concise support agent." },
    { role: "user", content: "How do I reset my password?" },
  ],
  temperature: 0.3,
  max_tokens: 400,
  routing: { mode: "preferred", allow_fallback: true },
  metadata: { feature: "help-center" },
});

completion.tokens; // { prompt, completion, total }
completion.cost_estimate; // USD
completion.attempts; // every provider call, including failed fallbacks
```

List what can be routed to:

```ts
const { models } = await cortex.router.models();
for (const m of models.filter((m) => m.available && m.allowed)) {
  console.log(m.id, m.capabilities, m.pricing);
}
```

Grounded answers with numbered citations come from the `knowledge` option;
conversational memory comes from `memory` (see below). Both work with
streaming too.

```ts
const answer = await cortex.chat.complete({
  messages: [{ role: "user", content: "How long do refunds take?" }],
  knowledge: { top_k: 5, min_confidence: 0.3 },
});
answer.knowledge?.citations.forEach((c) => console.log(`[${c.index}] ${c.label}`));
```

## Streaming

```ts
const stream = cortex.chat.stream({
  messages: [{ role: "user", content: "Write a haiku about latency." }],
});

for await (const event of stream) {
  switch (event.type) {
    case "start":
      console.log(`routing to ${event.provider}/${event.model}`);
      break;
    case "token":
      process.stdout.write(event.delta);
      break;
    case "complete":
      console.log("\n", event.completion.tokens);
      break;
    case "error":
      console.error(event.error); // a typed CortexError
      break;
  }
}
```

Or skip the event plumbing:

```ts
for await (const text of cortex.chat.stream({ messages }).textStream()) process.stdout.write(text);
const completion = await cortex.chat.stream({ messages }).finalCompletion();
```

- Nothing is sent until you start iterating, and a stream can be consumed once.
- Failures, including HTTP errors before the stream opens, arrive as a final
  `error` event. `textStream()` and `finalCompletion()` throw them instead.
- `stream.abort()` (or an `AbortSignal` in the request options) cancels it.
- The server currently runs the provider call to completion and then streams
  the output as `token` events, so time to first token equals the full
  latency. Native provider streaming is planned; the event protocol will not
  change.

## Memory

Conversations keep a hot Redis session backed by Postgres; long-term memories
are ranked by relevance, importance, and recency.

```ts
const conversation = await cortex.memory.createConversation({ title: "Support" });

await cortex.memory.addMessage({
  conversation_id: conversation.id,
  role: "user",
  content: "I prefer email over phone calls.",
});

// Let the router prepend history and relevant memories, then persist the turn.
const reply = await cortex.chat.complete({
  messages: [{ role: "user", content: "How should you contact me?" }],
  memory: { conversation_id: conversation.id },
});

const context = await cortex.memory.getContext(conversation.id, { memoryLimit: 5 });
await cortex.memory.storeMemory({
  type: "preference",
  content: "Customer prefers email follow-ups.",
  importance: 0.8,
});
const { memories } = await cortex.memory.searchMemories({ query: "contact preference" });

for await (const c of cortex.memory.iterConversations()) console.log(c.id, c.message_count);
```

## Knowledge

```ts
const result = await cortex.knowledge.search("How much paid leave do we get?");
// or: search({ query, top_k: 8, mode: "hybrid", filters: { sources: ["handbook.pdf"] } })

result.context.text; // numbered sources, ready for a system prompt
result.context.citations; // what each [n] refers to
result.context.confidence; // { score, level: "high" | "medium" | "low" | "none", ... }
result.results; // ranked chunks with vector and keyword scores
```

`cortex.knowledge.metrics()`, `listQueries()`, and `getQuery(id)` expose
retrieval quality and the query log.

## Documents

```ts
import { openAsBlob } from "node:fs";

const doc = await cortex.documents.upload(await openAsBlob("handbook.pdf"), {
  filename: "handbook.pdf", // required unless you pass a File; drives format detection
  metadata: { team: "people" },
  chunking: { chunk_size: 400, chunk_overlap: 40 },
});
console.log(doc.chunk_count, doc.ingestion.ingestion_ms);

await cortex.documents.create({ title: "FAQ", content: "# FAQ\n...", mime_type: "text/markdown" });
const page = await cortex.documents.list({ limit: 20 });
const detail = await cortex.documents.get(doc.id, { includeChunks: true });
await cortex.documents.delete(doc.id);
```

PDF, DOCX, Markdown, and plain text are supported. Uploading identical content
twice throws `ConflictError`, whose `documentId` is the existing copy.
Ingestion calls default to a 120 s timeout.

## Live events

```ts
const stream = await cortex.observatory.events({
  onEvent: (event) => console.log(event.type, event.data),
  onStatus: (status) => console.log("stream", status),
  onReady: ({ reconnected }) => {
    if (reconnected) void reload(); // pub/sub keeps no history
  },
});
// later
stream.stop();
```

The stream is a WebSocket to `/v1/events`. It reconnects with jittered backoff,
treats a missed heartbeat as a dead socket, and stops with status `refused`
when the server rejects it (bad key, wrong organization, disallowed origin).
The API key travels as the `cortex.bearer.<key>` subprotocol, since WebSockets
cannot carry headers, and is resolved again on every reconnect. See
[docs/protocols.md](../../docs/protocols.md#live-events).

Browsers, Bun, Deno, and Node 22+ provide a global `WebSocket`. On Node 20,
pass one in: `events({ WebSocket: (await import("ws")).default, ... })`.

## Errors

Every error extends `CortexError`. HTTP failures extend `APIError`, which
carries `status`, `body`, `detail`, `headers`, and `requestId`.

| Class                     | When                                                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `AuthenticationError`     | 401: missing, invalid, or revoked key.                                                                                  |
| `PermissionDeniedError`   | 403: organization mismatch or routing policy.                                                                           |
| `NotFoundError`           | 404.                                                                                                                    |
| `ConflictError`           | 409: duplicate document (`documentId`).                                                                                 |
| `ValidationError`         | 400/413/415/422 from the server (`issues` per field), or `status: null` when the SDK rejected the input before sending. |
| `RateLimitError`          | 429 after retries (`retryAfterMs`).                                                                                     |
| `ProviderError`           | 502/503: no provider could serve it (`attempts`, `completionId`).                                                       |
| `InternalServerError`     | Other 5xx.                                                                                                              |
| `NetworkError`            | No response. Subclasses: `TimeoutError`, `AbortError`.                                                                  |
| `ResponseValidationError` | A 2xx whose body is not the documented shape.                                                                           |

```ts
import { ProviderError, RateLimitError, ValidationError } from "@celestra/cortex-sdk";

try {
  await cortex.chat.complete({ messages });
} catch (error) {
  if (error instanceof ValidationError) console.error(error.issues);
  else if (error instanceof RateLimitError) console.error(`retry in ${error.retryAfterMs}ms`);
  else if (error instanceof ProviderError) console.error(error.attempts);
  else throw error;
}
```

## Retries, timeouts, and request IDs

- Up to `maxRetries` (default 2) retries with exponential backoff and jitter,
  honoring `Retry-After`. Configure with `retry: { maxRetries, initialDelayMs, maxDelayMs }`.
- Every call retries on 429, 503, and connection failures. Only calls that are
  safe to repeat (GET, DELETE, and `knowledge.search`) also retry after
  timeouts and 408/500/502/504, so a completion is never billed twice.
- `timeoutMs` (default 60 s) applies per attempt.
- Each call gets an `X-Request-ID` (`req_<hex>`), reused across its retries and
  echoed by the server. Errors expose it as `requestId`; quote it in bug reports.

Every method accepts per-call options last:

```ts
await cortex.router.models({ timeoutMs: 5_000, maxRetries: 0, requestId: "job-42", signal });
const eu = cortex.withOptions({ organizationId: "…", headers: { "X-Region": "eu" } });
```

## Middleware

Middleware wraps every attempt (outermost first) and may read or mutate the
request, including headers:

```ts
import {
  Cortex,
  headersMiddleware,
  loggingMiddleware,
  telemetryMiddleware,
  tracingMiddleware,
  type Middleware,
} from "@celestra/cortex-sdk";

const timing: Middleware = async (request, next) => {
  const started = performance.now();
  const response = await next(request);
  console.log(request.operation, request.attempt, response.status, performance.now() - started);
  return response;
};

const cortex = new Cortex({
  middleware: [
    loggingMiddleware(), // one line per attempt, no bodies
    telemetryMiddleware((e) => metrics.histogram("cortex.ms", e.durationMs, e)),
    headersMiddleware({ "X-App": "billing" }),
    tracingMiddleware({ current: () => otelTraceparent() }), // W3C traceparent
    timing,
  ],
});
cortex.use(anotherMiddleware);
```

Without an active trace, `tracingMiddleware` derives the trace ID from the
request ID, so client and server logs join on either value.
