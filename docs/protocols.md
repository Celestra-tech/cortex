# Protocols

The wire contracts every Cortex client relies on. Endpoint-by-endpoint
reference lives in [apps/api/README.md](../apps/api/README.md) and the
OpenAPI document served at `/docs`; this page defines the conventions shared
by all of them. Both SDKs implement these rules, and changes to them follow
the RFC process in [CONTRIBUTING.md](../CONTRIBUTING.md#rfcs).

## Versioning

- Public endpoints live under `/v1`. Health endpoints (`/health`,
  `/health/*`) are unversioned.
- Within `/v1`, changes are additive: new endpoints, new optional request
  fields, and new response fields. Clients must ignore unknown response fields.
- Removing or renaming a field, changing its type or meaning, or tightening
  validation is a breaking change and ships only under a new version prefix.

## Authentication

Every `/v1` request carries an organization API key:

```http
Authorization: Bearer ctx_4yJ0...
```

- The key identifies its organization. An optional `X-Organization-ID` header
  may accompany it and must match, or the request fails with `403`.
- Keys have a role. `member` keys use the platform; `admin` keys can also
  manage API keys (`/v1/api-keys`).
- Expired and revoked keys fail with `401` and `WWW-Authenticate: Bearer`.
- Development servers started with `CORTEX_AUTH_REQUIRE_API_KEY=false` also
  accept `X-Organization-ID` alone. Staging and production refuse to start in
  that mode.

Operator endpoints under `/v1/admin` use a separate bearer token
(`CORTEX_ADMIN_TOKEN`). When the token is not configured, those endpoints
return `404`.

## Requests

- Bodies are JSON (`Content-Type: application/json`), except document upload,
  which is `multipart/form-data`.
- Bodies larger than `CORTEX_API_MAX_REQUEST_BYTES` fail with `413`.
- Identifiers are UUIDv7 strings, so they sort by creation time.
- Timestamps are ISO 8601 with a UTC offset.

### Request IDs

Every response carries `X-Request-ID`. A client may send its own
(`[A-Za-z0-9._:-]`, up to 128 characters) to correlate logs; otherwise the
server generates one. SDKs send one ID per logical call and reuse it across
retries. Include it in bug reports.

## Responses

### Errors

Every error response has a JSON body with a `detail` field:

```json
{ "detail": "Invalid or revoked API key" }
```

Validation failures (`422`) from request parsing return a list of issues:

```json
{
  "detail": [{ "loc": ["body", "messages"], "msg": "Field required", "type": "missing" }]
}
```

Some errors add context next to `detail`. For example, a completion that
failed on every provider (`502`) lists each attempt.

| Status | Meaning                                                                      |
| ------ | ---------------------------------------------------------------------------- |
| `400`  | Malformed request                                                            |
| `401`  | Missing, invalid, expired, or revoked credentials                            |
| `403`  | Key belongs to another organization, insufficient role, or blocked by policy |
| `404`  | Resource does not exist in this organization                                 |
| `409`  | Conflict, such as a duplicate document or a taken slug                       |
| `413`  | Request body too large                                                       |
| `422`  | Validation failed, or an unknown model                                       |
| `429`  | Rate limited; see `Retry-After`                                              |
| `502`  | Every upstream provider failed                                               |
| `503`  | No provider can serve the request, or a dependency is down                   |

Resources in other organizations return `404`, never `403`, so their
existence is not disclosed.

### Pagination

Collections use limit and offset and return the total:

```http
GET /v1/documents?limit=20&offset=40
```

```json
{ "items": [ ... ], "total": 137, "limit": 20, "offset": 40 }
```

Items are newest first unless documented otherwise. `limit` is clamped to the
endpoint's maximum. Both SDKs provide iterators that walk every page.

### Rate limits

Requests are counted per API key (or per organization without one) in a
fixed one-minute window. Counted responses carry:

| Header                  | Meaning                         |
| ----------------------- | ------------------------------- |
| `X-RateLimit-Limit`     | Requests allowed in the window  |
| `X-RateLimit-Remaining` | Requests left in the window     |
| `X-RateLimit-Reset`     | Seconds until the window resets |

A limited request fails with `429` and `Retry-After` in seconds. SDKs retry
it automatically.

### Retries

Clients may safely retry `429`, `503`, and connection failures for every
request. Timeouts and other `5xx` responses should be retried only for
idempotent requests (`GET`, `DELETE`), so a completion or an API key is never
created twice. Retries use exponential backoff with jitter and honor
`Retry-After`.

## Completion streaming

`POST /v1/chat/completions` with `"stream": true` responds with
`text/event-stream`. Each event has a name and a single-line JSON payload:

```
event: start
data: {"id":"0198f7a2-...","created_at":"2026-09-27T12:00:00+00:00","provider":"anthropic","model":"claude-sonnet-4-5","routing_reason":"..."}

event: token
data: {"index":0,"delta":"Hello"}

event: token
data: {"index":1,"delta":" there"}

event: complete
data: { ...the same body as a non-streamed completion... }
```

| Event      | Payload                                                                                                                               | Count        |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------- | ------------ |
| `start`    | `id`, `created_at`, `provider`, `model`, `routing_reason`                                                                             | Exactly one  |
| `token`    | `index` (from 0), `delta` (text to append)                                                                                            | Zero or more |
| `complete` | The full completion response, including tokens, cost, attempts                                                                        | At most one  |
| `error`    | The error body (`detail`, plus fields such as `attempts`) and `status`, the equivalent HTTP status (clients assume `502` when absent) | At most one  |

A stream ends after `complete` or `error`. Routing, fallback, memory, and
grounding run before the stream opens, so failures in those stages are
ordinary HTTP error responses. The provider call currently completes before
tokens are emitted; native upstream streaming will keep this protocol, and
`error` is reserved for failures that happen after the stream has opened.

## Live events

`GET /v1/events` upgrades to a WebSocket that delivers the organization's
operational events in real time.

### Connecting

Browsers cannot set headers on a WebSocket, so the API key is offered as a
subprotocol next to the protocol name:

```js
new WebSocket("wss://api.example.com/v1/events", ["cortex.events.v1", "cortex.bearer.ctx_4yJ0..."]);
```

The server answers with `cortex.events.v1` and never echoes the key. Non-browser
clients may send `Authorization: Bearer <key>` instead. An optional
`organization_id` query parameter must match the key's organization.
Without a key, development servers accept `organization_id` alone; servers
that require keys refuse that connection.

Browser connections must come from an origin in `CORTEX_API_CORS_ORIGINS`.

### Frames

Every frame is a JSON text message with a `type` and a `data` object.

```json
{ "type": "stream.ready", "data": { "organization_id": "0198f7a2-..." } }
```

```json
{
  "id": "0198f7a3-...",
  "type": "execution.completed",
  "organization_id": "0198f7a2-...",
  "occurred_at": "2026-09-27T12:00:01+00:00",
  "data": {
    "completion_id": "0198f7a3-...",
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "latency_ms": 812.4
  }
}
```

```json
{ "type": "stream.ping", "data": {} }
```

- `stream.ready` is sent once, after the subscription is active.
- Events follow as they are published. Types: `request.received`,
  `execution.completed`, `execution.failed`, `document.ingesting`,
  `document.indexed`, `document.failed`, `document.deleted`,
  `memory.conversation_created`, `memory.conversation_deleted`,
  `memory.message_appended`, `memory.stored`.
- `stream.ping` is sent after every quiet heartbeat interval (default 20
  seconds, `CORTEX_EVENTS_HEARTBEAT_SECONDS`). Treat three missed heartbeats as a dead connection.
- Clients must ignore frame types they do not recognize.

Events are not stored. After a reconnect, reload current state through the
HTTP API; `stream.ready` marks the point from which events are complete.

### Close codes

| Code   | Meaning                                                                                  | Client action                     |
| ------ | ---------------------------------------------------------------------------------------- | --------------------------------- |
| `1000` | Closed normally                                                                          | None                              |
| `1008` | Refused: bad or missing key, wrong organization, disallowed origin, unknown organization | Do not retry; fix the credentials |
| `1011` | Server error, such as the event bus being unavailable                                    | Reconnect with backoff            |
| other  | Network failure                                                                          | Reconnect with backoff            |

The close reason carries a short human-readable explanation. The TypeScript
SDK's `CortexEventStream` implements this contract, including reconnection
with jittered backoff and heartbeat monitoring.

## Health

| Endpoint                | Meaning                                        | Status        |
| ----------------------- | ---------------------------------------------- | ------------- |
| `GET /health`           | Liveness: the process is serving               | `200`         |
| `GET /health/ready`     | Readiness: PostgreSQL and Redis are reachable  | `200` / `503` |
| `GET /health/database`  | PostgreSQL reachable                           | `200` / `503` |
| `GET /health/redis`     | Redis reachable                                | `200` / `503` |
| `GET /health/providers` | Provider credentials and circuit-breaker state | `200` / `503` |

A `503` still returns the full body so callers can see which dependency
failed. Health endpoints need no credentials and make no provider calls.
