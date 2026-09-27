# Changelog

Notable changes to CELESTRA Cortex are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/). Detailed, commit-level notes for
every release are generated from Conventional Commits and published on
[GitHub Releases](https://github.com/Celestra-tech/cortex/releases).

## [Unreleased]

The first public release, `1.0.0-alpha`, completes the v0.1 Foundation
milestone and the alpha of the Production Runtime.

### Added

- **Persistence.** PostgreSQL through async SQLAlchemy 2 and Alembic:
  UUIDv7 keys, timestamp and soft-delete mixins, JSONB, indexed foreign keys,
  a generic repository, validated organization and user schemas, and a
  `/health/database` probe. `alembic check` guards against model drift.
- **Router.** One completion API across OpenAI, Anthropic, Gemini, and
  OpenAI-compatible hosts. Objective-based model selection (`balanced`,
  `quality`, `speed`, `cost`), routing modes, organization routing policy,
  fallback with transient retries, circuit breakers, latency tracking, and cost
  estimates. Every provider attempt is recorded and queryable through
  `/v1/executions`.
- **Streaming.** Server-sent events (`start`, `token`, `complete`, `error`) for
  completions.
- **Memory.** Conversations and messages with a Redis session window backed by
  PostgreSQL; long-term memories with ranked recall; memory grounding in
  completions.
- **Knowledge.** Ingestion of PDF, DOCX, Markdown, and text; semantic chunking;
  local, OpenAI, and Gemini embeddings with isolated embedding spaces; hybrid
  pgvector and full-text retrieval fused with reciprocal rank fusion; numbered
  citations, confidence scoring, a retrieval log, and knowledge grounding in
  completions.
- **Evidence Graph.** Every decision is traceable. Completions are recorded
  as decisions linked to the memories, messages, conversations, documents,
  chunks, retrievals, and model runs that produced them; external decisions
  can be recorded through `POST /v2/evidence/decisions`. Typed edges
  (`supports`, `references`, `derived_from`, `retrieved_from`,
  `generated_by`, `contradicts`) carry provenance: confidence, explanation,
  source, and timestamp. `/v2/evidence` serves ranked supporting evidence,
  contradictions, bounded graphs with a provenance timeline, node detail, and
  shortest paths, with matching `evidence` resources in both SDKs.
- **Scenario Simulator.** Scenario planning for recorded decisions through
  `/v2/scenarios` and `/v2/decisions/{id}/scenarios`. Five deterministic
  stances (best case, base case, worst case, aggressive, conservative) turn a
  decision's evidence, constraints, and stated assumptions into linked
  assumptions and outcomes, scored on evidence quality, uncertainty, constraint
  satisfaction, objective alignment, and risk exposure with adjustable weights
  and risk tolerance. Matching `scenarios` resources in both SDKs.
- **Observatory.** Usage, latency, provider mix, and cost overview; a live event
  stream over WebSocket; Prometheus metrics; OpenTelemetry traces; structured
  JSON logs.
- **Security.** Organization API keys with `admin` and `member` roles, expiry,
  and rotation with a grace period; an operator endpoint and CLI to bootstrap
  organizations; per-key rate limiting; security headers, CORS, host, and body
  size enforcement; production settings that refuse unsafe defaults.
- **SDKs.** `@celestra/cortex-sdk` for TypeScript and `celestra-cortex` for
  Python (sync and asyncio) with the same surface, typed errors, retries with
  backoff, request IDs, streaming, pagination helpers, and middleware.
- **Dashboard.** Operator console with API key sign-in, system health, the
  execution log, and an interactive Evidence Graph with a node and edge
  inspector, confidence shading, and a provenance timeline. The Scenario
  Center runs simulations and compares scenarios with cards, a criteria
  table, an assumptions panel, and impact and confidence charts.
- **Operations.** Non-root production images, Docker Compose for local
  development, Cloud Run and Vercel deployment for staging and production,
  monitoring and alert policies, and CI, deploy, and release workflows.
- **Examples.** Quickstart (TypeScript and Python), a streaming chatbot with
  memory, and an enterprise onboarding flow.

### Security

- The live event stream (`/v1/events`) requires an API key whenever the API
  requires keys. Browsers pass the key as the `cortex.bearer.<key>` WebSocket
  subprotocol; see [docs/protocols.md](docs/protocols.md#live-events).

[Unreleased]: https://github.com/Celestra-tech/cortex/commits/main
