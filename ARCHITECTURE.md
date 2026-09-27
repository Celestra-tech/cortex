# Architecture

Cortex is an infrastructure layer between applications and foundation models.
This document describes its shape and the principles behind it. Component
internals (data model, routing algorithm, retrieval pipeline, SDK transport)
are in [docs/architecture.md](docs/architecture.md); wire-level contracts are
in [docs/protocols.md](docs/protocols.md).

## System context

```
  Applications ── SDKs (TypeScript, Python) ──┐
  Operators ───── Dashboard ──────────────────┤
                                              ▼
                              ┌───────────────────────────────┐
                              │           Cortex API          │
                              │                               │
                              │   Router       Memory         │
                              │   Knowledge    Observatory    │
                              │   Auth         Policy         │
                              └──────┬─────────────┬──────────┘
                                     │             │
                      PostgreSQL + pgvector      Redis
                      (system of record)   (sessions, cache,
                                            rate limits, events)
                                     │
                     ┌───────────────┼────────────────┐
                   OpenAI        Anthropic         Gemini   ··· OpenAI-compatible hosts
```

## Layers

The API follows Clean Architecture. Dependencies point inward: transport
depends on services, services depend on repositories and domain types, and
nothing inside depends on FastAPI, a vendor SDK, or a wire format.

| Layer             | Location (`apps/api/src/cortex_api/`) | Responsibility                                                        |
| ----------------- | ------------------------------------- | --------------------------------------------------------------------- |
| Transport         | `api/`                                | HTTP and WebSocket routes, request validation, authentication, errors |
| Schemas           | `schemas/`                            | Public request and response contracts (Pydantic)                      |
| Services          | `services/`                           | Use cases: routing, memory, knowledge, observatory, API keys          |
| Repositories      | `repositories/`                       | The only code that queries PostgreSQL; one per aggregate              |
| Models            | `models/`                             | SQLAlchemy entities; schema owned by Alembic migrations               |
| Core              | `core/`                               | Configuration, security, rate limiting, logging, metrics, tracing     |
| Provider adapters | `services/router/providers/`          | Vendor wire formats, isolated behind one interface                    |

`services/` at the repository root holds the same domains as independent
packages. Each starts as a library inside the API and can become its own
deployable without changing callers.

## Request lifecycle

A grounded, memory-aware completion:

1. **Authenticate.** The API key resolves the organization. Every query below
   is scoped to it.
2. **Rate limit.** A per-key window in Redis; the response carries
   `X-RateLimit-*` headers.
3. **Assemble context.** Memory loads the conversation window from Redis
   (falling back to PostgreSQL) plus ranked long-term memories. Knowledge runs
   hybrid retrieval and returns numbered, cited passages with a confidence
   score.
4. **Route.** The routing policy filters models by organization policy and
   capability, scores them for the requested objective, and produces a plan.
5. **Execute.** The fallback executor calls the first provider, retries
   transient failures, and moves down the plan on errors or open circuit
   breakers.
6. **Record.** Every attempt is written to `model_executions` with latency,
   tokens, and cost. The turn is persisted to memory, and events are published
   to the organization's live stream.
7. **Respond.** One provider-independent response, as JSON or as a server-sent
   event stream.

## Principles

- **Model-agnostic.** No vendor type crosses the adapter boundary. Adding a
  provider means adding an adapter, not changing callers.
- **PostgreSQL is the system of record.** Redis is disposable: losing it costs
  latency, never data.
- **Tenant isolation by construction.** Organization scope comes from the
  authenticated principal and is applied in repositories, not left to callers.
- **Everything is observable.** Every provider call is logged, metered, and
  traced; every request carries an `X-Request-ID`.
- **Contracts before code.** Public HTTP shapes live in `schemas/` and
  `packages/types`, change additively within a version, and are covered by
  tests in both SDKs.
- **Boring operations.** Stateless API instances, migrations as a separate
  job, configuration validated at startup, and production settings that
  refuse unsafe defaults.

## Deployment

| Component | Development                  | Production                                          |
| --------- | ---------------------------- | --------------------------------------------------- |
| API       | `pnpm dev` or Docker Compose | Cloud Run with an OpenTelemetry collector sidecar   |
| Dashboard | `pnpm dev` or Docker Compose | Vercel                                              |
| Data      | Local PostgreSQL and Redis   | Cloud SQL (PostgreSQL 17, pgvector) and Memorystore |

See [docs/deployment.md](docs/deployment.md).
