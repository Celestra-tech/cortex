<div align="center">

# CELESTRA Cortex

**The Intelligence Infrastructure**

Open-source infrastructure for building production AI systems with memory,
reasoning, knowledge, and multi-model orchestration.

[![CI](https://github.com/Celestra-tech/cortex/actions/workflows/ci.yml/badge.svg)](https://github.com/Celestra-tech/cortex/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Status](https://img.shields.io/badge/status-alpha-orange.svg)

[Quick start](#quick-start) · [Architecture](ARCHITECTURE.md) · [Protocols](docs/protocols.md) · [Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md)

</div>

---

## What is Cortex?

Cortex is not a model and not a chatbot. It is the layer that sits above
foundation models and gives every application the same production foundation:

- **Multi-model routing.** One API in front of OpenAI, Anthropic, Gemini, and
  OpenAI-compatible hosts. Cortex chooses the model per request, falls back
  across providers, and records every attempt.
- **Long-term memory.** Conversations and durable memories that follow a user
  across sessions, ranked by relevance, importance, and recency.
- **Knowledge retrieval.** Document ingestion and hybrid vector and keyword
  search in PostgreSQL, returned as cited context with a confidence score.
- **Reasoning orchestration.** Memory and knowledge are assembled into each
  completion's context by the platform, not by every application.
- **Decision Intelligence.** Policy-driven decisions over model choice, cost,
  and risk, with a full audit trail. Routing policy ships today; the Decision
  Engine is on the [roadmap](ROADMAP.md).
- **SDKs.** Typed TypeScript and Python clients with retries, streaming, and
  middleware built in.

```
                 Applications
                       │
     ┌─────────────────┴─────────────────┐
     │               Cortex              │
     │  routing · memory · knowledge     │
     │  reasoning · policy · observatory │
     └─────────────────┬─────────────────┘
                       │
       GPT  •  Claude  •  Gemini  •  Llama
```

Applications talk to Cortex. Cortex talks to models. Swapping a provider,
adding memory, or grounding answers in company documents becomes a
configuration change instead of a rewrite.

## Features

| Area            | Capabilities                                                                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Router**      | Objectives (`balanced`, `quality`, `speed`, `cost`), per-organization policy, fallback chains, circuit breakers, latency tracking, cost estimates |
| **Memory**      | Conversations with a Redis hot path and PostgreSQL system of record, long-term memories with ranked recall                                        |
| **Knowledge**   | PDF, DOCX, Markdown, and text ingestion; semantic chunking; pgvector HNSW plus full-text search fused with RRF; citations and confidence          |
| **Observatory** | Execution log for every provider call, usage and cost overview, live event stream, Prometheus metrics, OpenTelemetry traces                       |
| **Security**    | Organization-scoped API keys with roles, expiry, and zero-downtime rotation; rate limiting; strict CORS and security headers                      |
| **SDKs**        | `@celestra/cortex-sdk` (TypeScript) and `celestra-cortex` (Python, sync and asyncio) with the same surface                                        |
| **Dashboard**   | Operator console for system health, executions, and costs                                                                                         |
| **Operations**  | Production Docker images, Cloud Run and Vercel deployment, CI, and release automation                                                             |

## Quick start

Requires Docker with Compose v2.

```bash
git clone https://github.com/Celestra-tech/cortex.git
cd cortex
cp .env.example .env          # add at least one provider key, e.g. OPENAI_API_KEY
docker compose up --build
```

Create an organization and an API key. The secret prints once.

```bash
docker compose exec api cortex-entrypoint cli create-organization --name "Acme" --slug acme
docker compose exec api cortex-entrypoint cli create-api-key --organization acme --name quickstart
export CORTEX_API_KEY=ctx_...  # the secret printed above
```

Send a routed completion:

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $CORTEX_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"objective": "balanced", "messages": [{"role": "user", "content": "Hello, Cortex."}]}'
```

Or use an SDK:

```ts
import { Cortex } from "@celestra/cortex-sdk";

const cortex = new Cortex({ apiKey: process.env.CORTEX_API_KEY });

const answer = await cortex.chat.complete({
  messages: [{ role: "user", content: "How long do refunds take?" }],
  knowledge: { top_k: 5 }, // ground the answer in ingested documents
});

console.log(answer.output, answer.knowledge?.citations);
```

```python
from cortex import Cortex

cortex = Cortex()  # reads CORTEX_API_KEY and CORTEX_BASE_URL

conversation = cortex.memory.create_conversation(title="Support")
reply = cortex.chat.complete(
    messages=[{"role": "user", "content": "How should you contact me?"}],
    memory={"conversation_id": conversation.id},
)
print(reply.output, reply.provider, reply.cost_estimate)
```

| Service   | URL                        |
| --------- | -------------------------- |
| Dashboard | http://localhost:3000      |
| API       | http://localhost:8000      |
| API docs  | http://localhost:8000/docs |

More in [examples/](examples/): a quickstart, a streaming chatbot with memory,
and an enterprise onboarding flow.

## Monorepo structure

```
apps/
  api/              FastAPI gateway: routing, memory, knowledge, observatory (Python 3.13)
  dashboard/        Operator console (Next.js 16, Tailwind CSS v4)
packages/
  sdk-ts/           @celestra/cortex-sdk    TypeScript SDK
  sdk-py/           celestra-cortex         Python SDK, sync and asyncio
  types/            @celestra/cortex-types  Shared API contracts
  ui/               @celestra/cortex-ui     Design system
  tsconfig/         Shared TypeScript configuration
services/
  router · memory · knowledge · auth · observatory
                    Domain boundaries, extracted from the API as they grow
examples/
  quickstart · chatbot · enterprise
docker/             Production images for the API and dashboard
infra/              Cloud Run, Vercel, monitoring, GitHub, and deploy scripts
docs/               Architecture, protocols, development, deployment, roadmap
.github/            CI, tests, deploys, release automation, templates
```

## Development

Prerequisites: Node 22 (`.nvmrc`), pnpm 9.15, uv, and Docker for PostgreSQL and
Redis.

```bash
pnpm install
uv sync --all-packages
docker compose up -d postgres redis
pnpm dev                     # API on :8000, dashboard on :3000
```

```bash
pnpm test                    # pytest and vitest
pnpm lint                    # ruff and eslint
pnpm typecheck               # mypy and tsc
pnpm build                   # production builds
pnpm format                  # prettier
```

The full guide, including database migrations and the test databases, is in
[docs/development.md](docs/development.md).

## Documentation

| Document                                      | Contents                                                 |
| --------------------------------------------- | -------------------------------------------------------- |
| [Architecture](ARCHITECTURE.md)               | Layers, request lifecycle, and design principles         |
| [Architecture in depth](docs/architecture.md) | Data model, router, memory, knowledge, SDK internals     |
| [Protocols](docs/protocols.md)                | HTTP conventions, authentication, streaming, live events |
| [API reference](apps/api/README.md)           | Endpoints, routing policy, rate limits, health, metrics  |
| [TypeScript SDK](packages/sdk-ts/README.md)   | Client reference                                         |
| [Python SDK](packages/sdk-py/README.md)       | Client reference                                         |
| [Development](docs/development.md)            | Local setup, tests, migrations                           |
| [Deployment](docs/deployment.md)              | Cloud Run, Vercel, monitoring, releases                  |
| [Roadmap](ROADMAP.md)                         | Milestones and status                                    |
| [Contributing](CONTRIBUTING.md)               | Engineering standards, RFCs, pull requests               |
| [Security](SECURITY.md)                       | Reporting vulnerabilities                                |

## Status

Cortex is in **alpha**. The v0.1 foundation (router, memory, knowledge) is
complete and runs in production form; APIs may still change before v1.0. See
the [roadmap](ROADMAP.md) and [changelog](CHANGELOG.md).

## License

Cortex is licensed under the [Apache License 2.0](LICENSE).
