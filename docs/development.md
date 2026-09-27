# Development

## Prerequisites

| Tool   | Version | Notes                              |
| ------ | ------- | ---------------------------------- |
| Node   | 20.9+   | 22 LTS recommended (`.nvmrc`)      |
| pnpm   | 9.15    | pinned via `packageManager`        |
| uv     | 0.8+    | installs Python 3.13 automatically |
| Docker | 24+     | Compose v2                         |

## Setup

```bash
cp .env.example .env
pnpm install
uv sync --all-packages
```

## Running

Everything in containers:

```bash
docker compose up --build
```

Apps on the host, infrastructure in containers (fastest feedback loop):

```bash
docker compose up -d postgres redis
pnpm dev
```

Single app:

```bash
pnpm --filter @celestra/cortex-api dev
pnpm --filter @celestra/cortex-dashboard dev
```

## Configuration

All configuration comes from environment variables; `.env.example` documents
every one. The API reads variables prefixed with `CORTEX_` (see
`apps/api/src/cortex_api/core/config.py`) and also loads the repository `.env`.
The dashboard reads its variables at request time, so one image serves any
environment.

## Database migrations

```bash
pnpm --filter @celestra/cortex-api db:revision -- -m "create widgets"
pnpm --filter @celestra/cortex-api db:upgrade
```

Register new models in `apps/api/src/cortex_api/models/__init__.py` so
autogenerate can see them, and run `uv run alembic check` before committing.

## Database and Redis tests

API database tests are skipped unless `CORTEX_TEST_DATABASE_URL` points at a
database whose name ends in `_test`. The compose Postgres creates
`celestra_cortex_test` on first start. Session-cache and memory API tests
also need `CORTEX_TEST_REDIS_URL`, which must select a non-default database.
Tests only delete their own randomly prefixed keys.

Knowledge tests also need pgvector installed on the server. The compose
service uses `pgvector/pgvector:pg17`, and the knowledge migration runs
`CREATE EXTENSION IF NOT EXISTS vector`. A plain PostgreSQL server fails that
migration with "extension vector is not available".

```bash
docker compose up -d postgres redis
export CORTEX_TEST_DATABASE_URL=postgresql+asyncpg://cortex:cortex@localhost:5432/celestra_cortex_test
export CORTEX_TEST_REDIS_URL=redis://localhost:6379/15
pnpm --filter @celestra/cortex-api test
```

## Quality gates

```bash
pnpm lint        # ruff check + ruff format --check, eslint
pnpm typecheck   # mypy --strict, tsc
pnpm test        # pytest, vitest
pnpm format:check
uv run ruff check examples infra && uv run ruff format --check examples infra
```

CI (`.github/workflows/ci.yml`) runs the same gates on every pull request, plus
the migration round trip, production image builds, and infrastructure checks.
The engineering standards behind them are in [contributing.md](contributing.md).

## Examples

[`examples/`](../examples) holds runnable programs for both SDKs. The
TypeScript examples are pnpm workspace packages, so `pnpm typecheck` covers
them:

```bash
pnpm --filter @celestra/cortex-example-quickstart start
uv run python examples/quickstart/main.py
```

## Adding UI components

shadcn/ui components live in `packages/ui`. From `apps/dashboard`:

```bash
pnpm dlx shadcn@latest add dialog
```

## Adding a Python service dependency to the API

```bash
uv add --package cortex-api cortex-router
```

Workspace members resolve locally through `[tool.uv.sources]`.
