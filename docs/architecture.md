# Architecture

Component internals of CELESTRA Cortex: runtime topology, data model, and how
the router, memory, knowledge, observatory, and SDKs work. The system overview
and design principles are in [ARCHITECTURE.md](../ARCHITECTURE.md); wire
contracts are in [protocols.md](protocols.md).

## Runtime topology

```
browser ──► dashboard (Next.js, :3000) ──► api (FastAPI, :8000) ──┬─► postgres (:5432)
                                                                   └─► redis (:6379)
```

- The **dashboard** renders on the server. It reaches the API over the internal
  Docker network (`CORTEX_API_URL=http://api:8000`), so the browser never calls
  the API directly and no CORS is involved for status checks.
- The **API** owns all access to PostgreSQL and Redis.
- **PostgreSQL** and **Redis** publish their ports on `127.0.0.1` only.

## Health model

| Endpoint                      | Meaning                                    | Status codes  |
| ----------------------------- | ------------------------------------------ | ------------- |
| `GET /health` (API)           | Liveness: the process is serving.          | `200`         |
| `GET /health/database` (API)  | PostgreSQL reachable.                      | `200` / `503` |
| `GET /health/ready` (API)     | Readiness: PostgreSQL and Redis reachable. | `200` / `503` |
| `GET /api/health` (dashboard) | Liveness of the Next.js server.            | `200`         |

Readiness probes run concurrently with a per-dependency timeout
(`CORTEX_HEALTH_CHECK_TIMEOUT_SECONDS`, default 2s). A `503` still returns the
full body so callers can see which dependency failed.

Compose uses container health checks to order startup:
`postgres`, `redis` → `api` → `dashboard`.

## Data model

```
organizations ─┬─< users ──────────┐
               ├─< api_keys        │ actor_id (SET NULL)
               ├─< audit_logs >────┘
               │  (organization_id SET NULL)
               ├─< conversations ─< messages
               ├─< memories >─ source_message_id (SET NULL) ─┘
               ├─< model_executions   (grouped by completion_id)
               ├─< documents ─< document_chunks ─< embeddings
               ├─< knowledge_queries  (completion_id links grounded completions)
               ├─< evidence_nodes ─< evidence_edges >─ evidence_nodes
               └─< scenarios >─ decision_node_id (evidence_nodes, CASCADE)
                     ├─< assumptions >─ evidence_node_id (SET NULL)
                     └─< outcomes >─ assumption_id (SET NULL)
```

| Table               | Notes                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------- |
| `organizations`     | Tenant root. Slug unique among live rows. `settings` JSONB holds the routing policy.    |
| `users`             | Belong to one organization. Email lowercased, unique per organization.                  |
| `api_keys`          | Organization credentials. Only `key_hash` is stored; soft delete revokes.               |
| `audit_logs`        | Append-only. `metadata` is JSONB with a GIN (`jsonb_path_ops`) index.                   |
| `conversations`     | Per-organization chat threads. Soft delete; `updated_at` bumps per message.             |
| `messages`          | Immutable turns: role, content, estimated `token_count`, JSONB `metadata`.              |
| `memories`          | Long-term memory: type, summary, content, importance, generated `search_vector` (GIN).  |
| `model_executions`  | Append-only log of every provider call: latency, tokens, cost, success, error.          |
| `documents`         | Ingested source: title, MIME type, JSONB metadata (GIN), SHA-256 unique per org, stats. |
| `document_chunks`   | Chunk text, offsets, section, pages, `vector(1536)` (HNSW) and weighted tsvector (GIN). |
| `embeddings`        | Each chunk's native-width vector per provider/model/dimensions.                         |
| `knowledge_queries` | Retrieval log: latency split, candidates, confidence, results, citations used.          |
| `evidence_nodes`    | Snapshots of decisions and their evidence; `ref_id` names the source record, no FK.     |
| `evidence_edges`    | Typed links with provenance: confidence, explanation, source, `observed_at`.            |
| `scenarios`         | One stance on a decision per row: score, confidence, rank, criteria and stance (JSONB). |
| `assumptions`       | What a scenario takes to be true: kind, statement, assumed and recorded confidence.     |
| `outcomes`          | What follows from the assumptions: result, impact (−1..1), likelihood.                  |

All keys are UUIDv7, all timestamps `timestamptz`, and every foreign key is
indexed. Organizations, users, and API keys support soft delete. Deleting an
organization cascades to its users and keys; audit logs keep their rows with
nulled references. Schema is owned by Alembic
(`apps/api/migrations`). A test fails if models and migrations drift.

## Memory

```
POST /v1/messages ──► Postgres (commit) ──► Redis session:{conversation_id}  (Lua: append, dedupe, trim, TTL 24h)
GET  …/context    ──► Redis hit ─────────► recent window  ─┐
                      miss / Redis down ──► Postgres rebuild ┴─► + ranked memories (Postgres FTS)
```

PostgreSQL is the system of record and Redis is a disposable cache: losing
Redis costs latency, never data. Retrieval is lexical (full-text search
weighted by importance and exponential recency decay). The `MemoryService` in
`apps/api/src/cortex_api/services/memory/` is the only writer, so embeddings
and vector search can be added behind the same interface later.

## Router

```
POST /v1/chat/completions
  └─ CompletionService ── memory grounding (optional) ─┐
       └─ CortexRouter                                 │
            ├─ RoutingPolicy  org policy → eligibility → objective score → plan
            ├─ FallbackExecutor  retry transient → next provider (openai → anthropic → gemini)
            │    └─ Provider adapters  OpenAI · Anthropic · Gemini (+ OpenAI-compatible hosts)
            └─ ProviderRegistry  catalog · circuit breakers · latency EWMA
  └─ model_executions (one row per attempt) ──► unified CompletionResponse
```

Cortex is model-agnostic: routing, retries, policy, and observability are
owned by Cortex, and vendor wire formats never leave `providers/`. Provider
health is tracked per API process; see `apps/api/README.md` for the scoring
model and policy format.

## Knowledge

```
POST /v1/documents[/ingest] ─► extract · normalize ─► semantic chunks ─► embed ─► documents · document_chunks · embeddings
POST /v1/knowledge/search   ─► vector (pgvector HNSW) ┐
                             └► keyword (Postgres FTS) ┴► RRF + recency ─► merged passages ─► [n] context + confidence
POST /v1/chat/completions {"knowledge": {}} ─► same context as a system message ─► cited [n] recorded
```

Retrieval runs entirely in PostgreSQL: pgvector for similarity, full-text
search for keywords, and JSONB containment for metadata filters. Every
filter applies inside both retrievers. Vectors are tagged with their embedding
space (`provider/model@dims`), so changing embedding models never compares
incompatible vectors. Redis caches query embeddings only. Every search writes
a `knowledge_queries` row, which feeds `GET /v1/knowledge/metrics`. Details
are in `apps/api/README.md`.

## Evidence Graph

```
POST /v1/chat/completions ─► execution log + decision node + evidence edges (one transaction)
POST /v2/evidence/decisions ─► external decision + evidence
GET  /v2/evidence/{id}[/graph] ─► recursive CTE (bounded) ─► in-memory traversal ─► ranked evidence · timeline
```

Every decision links to the exact memories, messages, conversations,
documents, chunks, retrievals, and model runs that produced it. Edges point
from evidence to what it informed and carry their provenance (confidence,
explanation, source, timestamp), so nothing in the trail is anonymous. The
dashboard's Evidence page renders the graph. Details are in
`apps/api/README.md`.

## Scenario Simulator

```
POST /v2/scenarios ─► decision's evidence (upstream walk) ─► five stances ─► assumptions ─► outcomes
                                                                  └─► five criteria ─► weighted score · confidence · rank
```

Scenario planning, not forecasting. Each scenario (best case, base case, worst
case, aggressive, conservative) applies an explicit stance to the same
evidence, constraints, and stated assumptions: how optimistic to be about the
evidence, how likely contradictions are to materialize, how much evidence to
set aside, how fully to commit, and how reliably constraints hold. Every
assumption is stored with its recorded and assumed confidence, its source, and
the evidence node it rests on, and every outcome links to the assumption that
drives it. Scores combine evidence quality, uncertainty, constraint
satisfaction, objective alignment, and risk exposure; the per-criterion
contributions add up to the score. The engine is deterministic and does not use
Redis. The dashboard's Scenario Center compares a decision's scenarios. Details
are in `apps/api/README.md`.

## Workspaces

Two workspace managers share one repository:

- **pnpm + Turborepo** for TypeScript (`apps/dashboard`, `packages/*`).
  `apps/api` has a thin `package.json` so `turbo run test|lint|typecheck` also
  drives the Python toolchain.
- **uv** for Python (`apps/api`, `packages/sdk-py`, `services/*`), locked in
  `uv.lock`. `packages/sdk-py` also has a thin `package.json` for Turborepo.

Shared TypeScript packages are source-only: consumers compile them directly via
Next.js `transpilePackages`, so there is no package build step.

## SDKs

`packages/sdk-ts` (`@celestra/cortex-sdk`) and `packages/sdk-py`
(`celestra-cortex`, sync and asyncio) expose the same surface: `chat`
(complete and stream), `memory`, `knowledge`, `documents`, `evidence`,
`scenarios`, `router`, and `system`. Both clients work the same way:

- **Auth.** Bearer API keys (`ctx_...`, SHA-256 hashed server-side), with an
  optional `X-Organization-ID` that must match the key.
- **Transport.** Middleware runs per attempt. Retries use exponential backoff
  with jitter and honor `Retry-After`. Every call retries on 429, 503, and
  connect failures; timeouts and other 5xx retry only for idempotent calls,
  so completions are never duplicated. One `X-Request-ID` covers a call and
  all its retries, and the server echoes it.
- **Types.** Inputs are validated before sending (Zod or Pydantic) and raise
  `ValidationError` with per-field issues. Responses are shape-checked but
  tolerate additive API changes.
- **Errors.** One hierarchy under `CortexError`, mapped from status codes:
  `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
  `ConflictError`, `ValidationError`, `RateLimitError`, `ProviderError`,
  `InternalServerError`, and `NetworkError` (including timeouts).
- **Streaming.** SSE (`start`, `token`, `complete`) is parsed into typed
  events, and failures become a final `error` event.

## Observatory

```
services ──► EventPublisher ──► Redis pub/sub  events:{organization_id}
                                     └─► /v1/events WebSocket (per connection subscription, heartbeats)
model_executions · knowledge_queries ──► /v1/observatory/overview (usage, latency, provider mix, cost)
HTTP middleware · provider calls ──► Prometheus (:9464) · OpenTelemetry traces · JSON logs
```

Events are fire-and-forget: publishing never fails a request, and nothing is
stored, so clients reload state after reconnecting. The stream authenticates
with the same API keys as HTTP; browsers offer the key as a WebSocket
subprotocol (see [protocols.md](protocols.md#live-events)). Metrics are served
on a separate port that is never exposed publicly.

## Services

`services/` holds the domain boundaries as independent uv workspace packages.
The implementations live in the API today (`apps/api/src/cortex_api/services/`)
and move here as each boundary stabilizes:

| Service       | Responsibility                               |
| ------------- | -------------------------------------------- |
| `router`      | Choose model and provider per request.       |
| `memory`      | Session and long-term agent memory.          |
| `knowledge`   | Ingestion, indexing, and retrieval.          |
| `auth`        | Identity, tenancy, API keys, access control. |
| `observatory` | Tracing, metrics, usage, and cost analytics. |

They start as libraries imported by the API; any can be promoted to its own
deployable later without changing the repository layout.

## Containers

- `docker/api/Dockerfile` — multi-stage uv build into `/opt/venv`, non-root.
  The entrypoint serves by default, runs `migrate` as a one-off job, and migrates
  before serving only when `CORTEX_RUN_MIGRATIONS=true` (docker compose).
- `docker/dashboard/Dockerfile` — `turbo prune` → frozen pnpm install → Next.js
  standalone output, non-root. Production serves the dashboard from Vercel instead.

Both images build from the repository root as context. Production runs the API
image on Cloud Run with an OpenTelemetry collector sidecar; see
[deployment.md](deployment.md).
