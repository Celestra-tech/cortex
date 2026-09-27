# cortex-api

FastAPI gateway for CELESTRA Cortex.

## Endpoints

| Method | Path               | Purpose                                                          |
| ------ | ------------------ | ---------------------------------------------------------------- |
| GET    | `/health`          | Liveness. Always `200` while the process is serving traffic.     |
| GET    | `/health/database` | PostgreSQL connectivity. `200` connected, `503` disconnected.    |
| GET    | `/health/ready`    | Readiness. Checks PostgreSQL and Redis; `503` if either is down. |
| GET    | `/health/redis`    | Redis round trip. `200` connected, `503` disconnected.           |
| GET    | `/health/providers` | Circuit state per model provider; `503` when none can serve. No provider calls. |
| GET    | `/docs`            | OpenAPI UI (disabled in staging and production).                 |

Prometheus metrics are served on a separate port (`CORTEX_METRICS_PORT`, default
`9464`), never on the public listener: HTTP requests, latency and in-flight by route
template, unhandled exceptions, 429s, and per-provider calls, latency, tokens, cost,
and circuit state. With `CORTEX_OTEL_ENABLED=true`, requests, SQL, Redis, and provider
calls are traced over OTLP/HTTP. See `docs/deployment.md` for the production setup.

### Authentication

Every `/v1` request is scoped to one organization. Send an API key:

```
Authorization: Bearer ctx_...
```

Keys are stored as SHA-256 digests plus a display prefix (`ctx_` and 8 characters);
the secret is shown once, at creation. Each key has a role, `admin` or `member`, an
optional expiry, and a `last_used_at` written at most once a minute.

| Method | Path                         | Purpose (admin keys only)                                              |
| ------ | ---------------------------- | ---------------------------------------------------------------------- |
| GET    | `/v1/api-keys`               | List keys, newest first (`include_revoked=true` for history).         |
| POST   | `/v1/api-keys`               | Create `{name, role, expires_in_days?}`; returns the secret once.       |
| GET    | `/v1/api-keys/{id}`          | One key's metadata and status (`active`, `expired`, `revoked`).        |
| POST   | `/v1/api-keys/{id}/rotate`   | New secret; the old key keeps working for `grace_period_seconds` (default 3600, `0` revokes now). |
| DELETE | `/v1/api-keys/{id}`          | Revoke. The last usable admin key cannot be revoked (`409`).           |

Every create, rotate, and revoke is written to the audit log with the acting key.
Platform operators bootstrap tenants with `POST /v1/admin/organizations`
(`Authorization: Bearer $CORTEX_ADMIN_TOKEN`; the route does not exist unless the
token is configured), which returns the organization and its first admin key. The
CLI does the same against the database directly:

```bash
uv run python -m cortex_api.cli create-organization --name "Acme" --slug acme
uv run python -m cortex_api.cli create-api-key --organization acme --name backend --role member  # secret on stdout
uv run python -m cortex_api.cli list-api-keys --organization acme
uv run python -m cortex_api.cli rotate-api-key --organization acme <key id> --grace-seconds 3600
uv run python -m cortex_api.cli revoke-api-key --organization acme <key id>
```

Requests are rate limited per key (per organization in header-only development
mode): `CORTEX_RATE_LIMIT_REQUESTS_PER_MINUTE` in a one-minute window, overridable
per organization with `settings.rate_limit_per_minute`. Responses carry
`X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset`; over the limit
returns `429` with `Retry-After`. Bodies over `CORTEX_API_MAX_REQUEST_BYTES` get `413`.

- An optional `X-Organization-ID` must match the key's organization (`403`
  otherwise).
- Without a key, `X-Organization-ID` alone names the tenant. This is for
  local development: set `CORTEX_AUTH_REQUIRE_API_KEY=true` to reject it.
- Missing, malformed, unknown, or revoked keys return `401` with
  `WWW-Authenticate: Bearer`.
- Resources belonging to another organization return `404`.

Every response carries `X-Request-ID`: the caller's value when it matches
`[A-Za-z0-9._:-]{1,128}`, otherwise a generated one. The SDKs send one per
call and reuse it across retries. The curl examples below use the
development fallback; swap in `-H "Authorization: Bearer $CORTEX_API_KEY"`
against a server that requires keys.

The typed SDKs, `packages/sdk-ts` and `packages/sdk-py`, wrap everything
below, including authentication, retries, and streaming.

### Memory (`/v1`)

| Method | Path                              | Purpose                                                           |
| ------ | --------------------------------- | ----------------------------------------------------------------- |
| POST   | `/v1/conversations`               | Create a conversation (`201`).                                    |
| GET    | `/v1/conversations/{id}`          | Conversation, message count, and the newest `message_limit` turns. |
| GET    | `/v1/conversations/{id}/context`  | Hot session window + ranked memories for the next model turn.     |
| DELETE | `/v1/conversations/{id}`          | Soft delete and drop the Redis session (`204`).                   |
| POST   | `/v1/messages`                    | Append a message; updates Postgres, then the Redis session.       |
| POST   | `/v1/memories`                    | Store a long-term memory (summary derived when omitted).          |
| GET    | `/v1/memories`                    | Search memories: `query`, repeated `type`, `min_importance`, `limit`. |

```bash
ORG=<organization uuid>
CONV=$(curl -s -X POST localhost:8000/v1/conversations -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{"title":"Support"}' | jq -r .id)
curl -s -X POST localhost:8000/v1/messages -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' \
  -d "{\"conversation_id\":\"$CONV\",\"role\":\"user\",\"content\":\"Hi\"}"
curl -s localhost:8000/v1/conversations/$CONV/context -H "X-Organization-ID: $ORG"
```

### Router (`/v1`)

| Method | Path                   | Purpose                                                                  |
| ------ | ---------------------- | ------------------------------------------------------------------------ |
| POST   | `/v1/chat/completions` | Routed completion with retries and fallback; one unified response shape. |
| GET    | `/v1/models`           | Catalog with pricing, capabilities, live availability, and policy.      |
| GET    | `/v1/executions`       | Provider-call log: `provider`, `model`, `success`, `completion_id`, paging. |
| GET    | `/v1/executions/{id}`  | One provider call.                                                       |

```bash
curl -s -X POST localhost:8000/v1/chat/completions -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{
    "objective": "cost",
    "messages": [{"role": "user", "content": "Summarize our Q3 goals"}],
    "memory": {"conversation_id": "'"$CONV"'"}
  }'
```

`"stream": true` answers with Server-Sent Events instead of JSON:

```
event: start     {"id", "created_at", "provider", "model", "routing_reason"}
event: token     {"index", "delta"}          (repeated)
event: complete  <the same body as a non-streamed response>
```

Routing, fallback, memory, and grounding run before the stream opens, so
failures there are ordinary HTTP errors. The provider call currently
completes before tokens are emitted; native upstream streaming will keep this
protocol.

Status codes: `403` blocked by organization policy, `422` invalid request or
unknown model, `502` every provider failed (the body lists each attempt),
`503` no provider can serve the request (the body lists why each model was
rejected).

### Knowledge (`/v1`)

| Method | Path                    | Purpose                                                                       |
| ------ | ----------------------- | ----------------------------------------------------------------------------- |
| POST   | `/v1/documents`         | Ingest inline text or Markdown (`201`).                                       |
| POST   | `/v1/documents/ingest`  | Multipart upload: PDF, DOCX, Markdown, or text (`201`).                       |
| GET    | `/v1/documents`         | List, newest first: `source`, `mime_type`, `limit`, `offset`.                 |
| GET    | `/v1/documents/{id}`    | Document with ingestion stats; `include_chunks=true` adds its chunks (paged). |
| DELETE | `/v1/documents/{id}`    | Hard delete, cascading to chunks and embeddings (`204`).                      |
| POST   | `/v1/knowledge/search`  | Hybrid search plus an assembled, citation-numbered context.                   |
| GET    | `/v1/knowledge/metrics` | Corpus size, ingestion and retrieval latency, quality, and citation usage.    |

```bash
curl -s -X POST localhost:8000/v1/documents/ingest -H "X-Organization-ID: $ORG" \
  -F file=@handbook.pdf -F 'metadata={"team":"people"}' -F chunk_size=400
curl -s -X POST localhost:8000/v1/knowledge/search -H "X-Organization-ID: $ORG" \
  -H 'Content-Type: application/json' -d '{
    "query": "How much paid leave do we get?",
    "top_k": 5,
    "filters": {"metadata": {"team": "people"}}
  }'
```

To ground a completion, add `"knowledge": {}` to `/v1/chat/completions`. The
response's `knowledge` field reports the citations, the confidence, and which
`[n]` markers the answer used.

Status codes: `409` identical content already ingested (the body carries
`document_id`), `413` over the upload, character, or chunk limit, `415`
unsupported file type, `422` unreadable file (encrypted or scanned PDFs,
corrupt DOCX), `502` embedding provider failed.

### Evidence (`/v2`)

Every decision is linked to the records that produced it. Completions are
recorded automatically; a decision is addressed by the id of what was decided,
so for completions `{decision_id}` is the completion id.

| Method | Path                               | Purpose                                                                                |
| ------ | ---------------------------------- | -------------------------------------------------------------------------------------- |
| GET    | `/v2/evidence`                     | Decisions, newest first: `limit` (≤ 200), `offset`.                                    |
| POST   | `/v2/evidence/decisions`           | Record an external decision with its evidence (`201`; `409` if the `ref_id` exists).   |
| GET    | `/v2/evidence/{decision_id}`       | The decision, supporting evidence strongest first, contradictions, counts by type.     |
| GET    | `/v2/evidence/{decision_id}/graph` | Nodes with signed depth, edges with provenance, a timeline, and a `truncated` flag.    |
| GET    | `/v2/evidence/node/{id}`           | One node by node id, its direct neighbors, and the decisions it fed.                   |
| GET    | `/v2/evidence/path`                | Shortest chain between two nodes: `source`, `target`, `directed`, `depth`.             |

Reads take `depth` (1-10, default 4).

```bash
curl -s localhost:8000/v2/evidence/$COMPLETION_ID -H "Authorization: Bearer $KEY"
curl -s -X POST localhost:8000/v2/evidence/decisions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' -d '{
    "title": "Approve refund for order 1042",
    "source": "policy-engine@3",
    "evidence": [
      {"type": "document", "ref_id": "'$DOC_ID'", "title": "Refund policy",
       "explanation": "Order is inside the 30-day window", "relation_confidence": 0.9}
    ]
  }'
```

## Evidence architecture

The Evidence Graph is an explainability layer, not a knowledge graph. It
lives in two tables in PostgreSQL:

- `evidence_nodes`: a snapshot of a decision, memory, message, conversation,
  document, chunk, knowledge retrieval, or model run (`benchmark`), with a
  title, a confidence, and `ref_id` pointing at the source record. Nodes have
  no foreign key to their source, so the trail survives when the source is
  deleted. `(organization_id, type, ref_id)` is unique, so the same memory or
  chunk is one node shared by every decision it informed.
- `evidence_edges`: `supports`, `references`, `derived_from`,
  `retrieved_from`, `generated_by`, or `contradicts`. Edges always point
  upstream to downstream, from the evidence to what it informed. `supports`
  and `contradicts` read forward ("memory supports decision"); the rest read
  from the downstream end ("chunk derived from document"). Every edge carries
  provenance: `confidence`, `explanation`, `source` (a `cortex.*` component
  or `api:key/<id>`), and `observed_at`.

Writes are first-write-wins (`ON CONFLICT DO NOTHING`), so provenance is never
rewritten. A completion's trail commits in the same transaction as its
execution log:

```
completion ──generated_by── model run (winning execution)
    ▲ supports    recalled memories (edge confidence = recall score)
    ▲ references  conversation; history messages ──derived_from──►
    ▲ supports    knowledge retrieval (references when grounding was off)
    │               ▲ retrieved_from  chunks ◄──derived_from── documents
    ▲ supports    cited chunks
    └──generated_by──► stored assistant reply; stored prompts ──derived_from──► decision
```

Traversal loads a bounded subgraph with one recursive CTE (depth ≤ 10, at most
2000 edges, `truncated` when cut short) and runs the algorithms in memory
(`services/evidence/traversal.py`): `ancestors`, `descendants`,
`shortest_path`, and `supporting_evidence`. Supporting evidence ranks every
upstream node by its strongest route: `path_confidence` is the product of edge
confidences and intermediate node confidences (max-product Dijkstra), and
`strength` multiplies in the node's own confidence. A decision's confidence is
a noisy-OR over its direct `supports` edges, discounted by each
`contradicts` edge; with no support it is 0.

## Knowledge architecture

```
upload ─► detect (magic bytes) ─► extract ─► normalize ─► chunk ─► embed (batched) ─► store
                                   PDF · DOCX · MD · TXT          provider              documents · document_chunks · embeddings
query  ─► embed (Redis cache) ─► vector top-N (HNSW) ─┐
       └─► tsquery ────────────► keyword top-N (GIN) ─┴─► RRF ─► recency ─► merge ─► budget ─► [n] context
```

- **Extraction** (`extraction.py`). The format comes from the file's magic
  bytes, then its declared type, then its extension. PDFs are read with
  `pypdf` and keep page offsets. DOCX is parsed with `defusedxml`, keeping
  headings as Markdown headings, list items, and table rows. Normalization
  applies NFKC, unifies newlines, strips control and zero-width characters,
  and collapses whitespace. Scanned PDFs are rejected with a clear error,
  since OCR is not included.
- **Chunking** (`chunker.py`). Markdown headings define sections, and each
  chunk records its heading path (`Guide > Setup`). Inside a section, text is
  split recursively on the configured separators (paragraph, line, sentence,
  clause, word), then packed up to `chunk_size` tokens. Each chunk overlaps the
  previous one by up to `chunk_overlap` tokens, starting on a sentence or word
  boundary. Chunks record exact character offsets, pages, and token counts.
  Size, overlap, and separators can be set per request.
- **Embeddings** (`embeddings.py`). Available providers:
  - `openai`: `text-embedding-3-*`. It also works with OpenAI-compatible
    servers (Ollama, vLLM, TEI) via `CORTEX_KNOWLEDGE_EMBEDDING_BASE_URL`.
  - `gemini`: `gemini-embedding-001`, with separate document and query task
    types.
  - `local`: an offline feature-hashing embedder. It matches words, not
    meaning, so use it for development and tests only.

  Requests are batched, run with bounded concurrency, and retried on transient
  errors (honouring `Retry-After`). Each chunk is embedded as
  `title > section` plus its content. Vectors are L2-normalized and stored in
  one `vector(1536)` column. Narrower models are zero-padded, which leaves
  cosine similarity unchanged.
- **Embedding spaces.** Every vector is tagged `provider/model@dims`, and
  vector search only compares vectors in the configured space. Switching model
  therefore never mixes incompatible vectors: documents ingested under the old
  model are found by keyword search only, until they are re-ingested. The
  `embeddings` table keeps each chunk's native-width vector.
- **Hybrid search** (`hybrid_search.py`). Two retrievers run for each query:
  - vector: pgvector HNSW, cosine distance, iterative scan so filters don't
    starve results;
  - keyword: PostgreSQL full-text search, with the section weighted above the
    body and query terms OR-ed.

  Each returns up to `4 × top_k` candidates (40–400). The two lists are fused
  with weighted Reciprocal Rank Fusion (`k=60`), normalized so 1.0 means "first
  in every list". A recency boost multiplies each score by
  `1 - w + w * 0.5^(age / half_life)`. Filters on document ids, sources, MIME
  types, JSONB metadata containment, and created-at range apply inside both
  retrievers. If the embedding provider is down, hybrid search falls back to
  keyword search and returns a warning.
- **Context assembly** (`assembler.py`, `citations.py`).
  - Adjacent hits from the same document are merged into one passage, with the
    overlap counted once.
  - Passages are packed best-first into `max_context_tokens`, each numbered
    `[n]` with a label (`Title > Section (p. 3)`) and a snippet.
  - **Confidence** is a heuristic, not a probability. It blends three signals:
    the best vector similarity (rescaled to the provider's typical range), the
    share of query terms the context covers, and the share of chunks both
    retrievers agreed on. It is labelled `high` (≥ 0.7), `medium` (≥ 0.4),
    `low`, or `none`.
- **Grounded completions.** The context goes in as a system message after the
  caller's own system messages, with instructions to cite `[n]`. Contexts
  below `knowledge.min_confidence` are withheld from the model. After the
  answer, the `[n]` markers are parsed and stored with the query.
- **Observability.**
  - Each document stores its ingestion and embedding time, plus a per-stage
    breakdown (extract, chunk, embed, store; tokens; batches).
  - Every search writes a `knowledge_queries` row: latency split into
    embedding, vector, and keyword time, candidate counts, cache hit,
    confidence signals, and the ranked results. Grounded completions add
    `completion_id` and the citations they used.
  - `GET /v1/knowledge/metrics` aggregates these over a time window: p50/p95
    latency, zero-result rate, cache hit rate, average confidence, and
    citation usage rate.
- **Limits.** Uploads are capped at 25 MiB, documents at 5M characters and
  10,000 chunks, and queries at 2,000 characters. Identical content is
  deduplicated per organization by SHA-256. Ingestion runs synchronously
  within the request, with CPU-bound extraction on a worker thread.

## Router architecture

Cortex owns routing. Applications send one request shape and receive one
response shape; vendor formats exist only inside `services/router/providers/`.

- **Registry** (`registry.py`) holds the catalog (`ModelSpec`: context window,
  output limit, USD per million tokens, relative quality, latency prior,
  capabilities). It tracks health per process: each provider has a circuit
  breaker (3 consecutive failures open it for 30s, then one probe is let
  through), and each model has a moving average of observed latency that
  replaces the catalog prior. A provider without a key is listed but never
  called.
- **Policies** (`policies.py`) turn a request into an ordered plan.
  - A model is eligible only if the organization policy allows it (allowed
    providers, blocked models, cost ceiling), its provider is available, it has
    the requested capabilities, and the prompt plus output fits its context
    and output limits.
  - Eligible models are scored:
    `w_quality * quality + w_latency * (1 - latency) + w_cost * (1 - cost)`.
    Latency and cost are compared on a log scale, so ratios matter rather than
    absolute differences. The weights come from the `objective` (`balanced`,
    `quality`, `speed`, `cost`).
  - `auto` picks the best score. `preferred` honours the requested model or
    provider, and reroutes (saying why in `routing_reason`) when it is
    unavailable. `strict` calls only the requested model and returns `503`
    otherwise.
- **Fallback** (`fallback.py`) retries transient errors (rate limits, timeouts,
  connection and 5xx errors) with capped exponential backoff and jitter,
  honouring `Retry-After`. It then moves to the best model of the next provider
  in `CORTEX_ROUTER_FALLBACK_ORDER` (default openai → anthropic → gemini). Auth
  and bad-request errors skip the retry. `strict` never falls back.
- **Observability.** Every provider call becomes a `model_executions` row:
  provider, model, latency, tokens, cost estimate, success, and error with its
  type. Rows are committed even when the whole completion fails. A completion's
  attempts share `completion_id`; the response `id` is that value.
- **Organization policy** lives at `organizations.settings.routing` and fails
  closed: an invalid policy returns `500` instead of being ignored.

  ```json
  {
    "routing": {
      "allowed_providers": ["anthropic", "gemini"],
      "blocked_models": ["claude-opus-4-1"],
      "default_objective": "cost",
      "allow_fallback": true,
      "fallback_order": ["anthropic", "gemini"],
      "max_cost_per_request_usd": 0.05
    }
  }
  ```

- **Memory grounding.** Pass `memory.conversation_id` and the completion is
  built from the request's system messages, then recalled long-term memories,
  then the conversation's hot session, then the new turns. The new turns and
  the output are appended back to the conversation.
- **New providers.** DeepSeek, Qwen, and Llama hosts speak the OpenAI protocol,
  so each is a registration of `OpenAIProvider` with its `ProviderName`, base
  URL, catalog, and `max_tokens_field="max_tokens"`; see
  `tests/router/test_providers.py`. Other protocols implement
  `HTTPProvider.complete()`.
- **Catalog prices** are estimates for ranking and reporting, not billing.
  Update the `*_MODELS` tuples when vendors change prices.

## Memory architecture

This layer covers hot session memory and persistent memory. Document
retrieval lives in Cortex Knowledge, described above.

- **PostgreSQL is the source of truth.** `conversations`, `messages`, and
  `memories` are written first, in one transaction per call.
- **Redis is the hot path.** `session:{conversation_id}` is a hash holding
  `messages` (a JSON array, oldest first), `token_count` and `last_activity`.
  Every write refreshes the 24h TTL. An atomic Lua script appends, dedupes by
  message id, and trims the window to `CORTEX_MEMORY_SESSION_MAX_MESSAGES`,
  then to `CORTEX_MEMORY_SESSION_MAX_TOKENS`, always keeping the newest
  message.
- **The cache is best effort.** Messages reach Redis only after the Postgres
  commit. On a cache miss, the window is rebuilt from Postgres and written
  back. If Redis is unreachable, requests still succeed from Postgres; the
  response's `source` field (`cache` or `database`) shows which path served it.
- **Retrieval** uses PostgreSQL full-text search over a generated, GIN-indexed
  `search_vector`. Query terms are OR-ed and scored with
  `ts_rank * (0.5 + importance) * 0.5^(age / half_life)`. Without a query,
  memories are ranked by `importance * recency`. Soft-deleted memories are
  never returned.
- **Token counts** are estimated at about 4 characters per token
  (`services/memory/encoder.py`). Swap in a model tokenizer via
  `TokenEstimator` when exact budgets matter.

## Layout

```
src/cortex_api/
  database/       config.py · base.py (Base) · mixins.py (id, timestamps, soft delete) · session.py · ids.py (UUIDv7) · types.py
  models/         organization · user · api_key · audit_log · conversation · message · memory · model_execution
                  document · document_chunk · embedding · knowledge_query
  repositories/   base.py (generic async CRUD) · memory_repository.py · execution_repository.py
                  knowledge_repository.py · …
  services/memory encoder (tokens, summaries) · session (Redis) · retrieval (FTS ranking) · service
  services/router base (contracts) · registry · policies · fallback · router · service · providers/
  services/knowledge  extraction · chunker · embeddings · ingestion · hybrid_search · citations
                      assembler · service
  schemas/        request/response models (organization.py, user.py, api_key.py, memory.py, …)
  api/            deps.py (settings, tenant, services) · errors.py · v1.py · routes/
  core/           settings, logging, health probes
migrations/       Alembic (async)
```

## Data layer conventions

- **Primary keys** are UUIDv7, generated in Python (time-ordered for index
  locality), with `gen_random_uuid()` as the server default for raw SQL inserts.
- **Timestamps** are `timestamptz`. `TimestampMixin` adds `created_at` and
  `updated_at`; a `BEFORE UPDATE` trigger keeps `updated_at` correct even for
  raw SQL. Audit logs only have `created_at`.
- **Soft delete** (`SoftDeleteMixin.deleted_at`) on organizations, users, and
  API keys. Repositories hide deleted rows unless `include_deleted=True`.
  Uniqueness (org slug, user email per org) is enforced by partial indexes over
  live rows, so identifiers can be reused after deletion.
- **Transactions** belong to the caller. Repositories `flush()`, never
  `commit()`. In routes, use the `DbSession` dependency and commit explicitly.
- **Relationships** use `lazy="raise"`. Load them explicitly with
  `selectinload()`, because implicit lazy loads are errors under asyncio.
- **Audit logs** are append-only: the repository rejects updates and deletes,
  and foreign keys use `ON DELETE SET NULL` so history outlives its subjects.
- **Schemas** in `schemas/` validate input before it reaches the database, and
  they mirror its constraints (slug format, lowercase email, role enum).
  `*Update` models are partial: `changes()` returns only the fields the client
  sent, ready for `repository.update(entity, **patch.changes())`.
- **Migrations** must match the models. `alembic check` fails on any drift, and
  the migration test runs it after upgrading to head.

```python
from cortex_api.database import DbSession
from cortex_api.repositories import OrganizationRepository


@router.post("/organizations")
async def create_organization(body: OrganizationIn, session: DbSession):
    organization = await OrganizationRepository(session).create(**body.model_dump())
    await session.commit()
    return organization
```

## Local development

```bash
uv sync --all-packages       # from the repo root; creates .venv with Python 3.13
pnpm --filter @celestra/cortex-api dev
```

Configuration is read from `CORTEX_`-prefixed environment variables
(`src/cortex_api/core/config.py`) and the repository `.env`.

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
uv run alembic check          # fails if models and migrations have drifted
```

New models must be imported in `cortex_api/models/__init__.py` so autogenerate
sees them. The Docker image runs `alembic upgrade head` on start unless
`CORTEX_RUN_MIGRATIONS=false`.

## Tests

```bash
uv run pytest
```

Database tests (migrations, connectivity, repositories) run only when
`CORTEX_TEST_DATABASE_URL` is set, and the database name must end in `_test`.
Each repository test runs inside a transaction that is rolled back. The local
compose stack creates `celestra_cortex_test` automatically:

The test database needs the pgvector extension installed on the server
(the compose image includes it); the migration creates it.

Redis tests (session cache, memory API) run only when `CORTEX_TEST_REDIS_URL`
is set to a non-default database. Each test uses a random key prefix and
deletes only its own keys.

```bash
docker compose up -d postgres redis
CORTEX_TEST_DATABASE_URL=postgresql+asyncpg://cortex:cortex@localhost:5432/celestra_cortex_test \
CORTEX_TEST_REDIS_URL=redis://localhost:6379/15 \
  uv run pytest
```

## Quality

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
```
