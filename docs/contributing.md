# Engineering standards

This is the detailed companion to [CONTRIBUTING.md](../CONTRIBUTING.md). It
explains each standard with the conventions the codebase already follows, so
new code reads like existing code.

## Clean Architecture

The API is organized in layers with dependencies pointing inward.

```
api/ (routes, deps)  ──►  services/  ──►  repositories/  ──►  models/
        │                     │
        ▼                     ▼
   schemas/ (public contracts)   services/router/providers/ (vendor adapters)
```

Rules:

- **Routes are thin.** A route validates input through a schema, resolves the
  principal and services through dependencies in `api/deps.py`, calls one
  service method, and maps the result to a response schema. Business rules do
  not live in routes.
- **Services own use cases.** They coordinate repositories, caches, and other
  services, and they own transaction boundaries.
- **Domain code is framework-free.** Services and repositories never import
  FastAPI. Raise domain errors (`NotFoundError`, `AuthenticationError`,
  `PermissionDeniedError`, `ConflictError`); `api/errors.py` maps them to HTTP.
- **Vendors stay behind adapters.** Only `services/router/providers/` knows a
  provider's wire format. Adding a provider means implementing the adapter
  interface and registering it in the catalog.
- **Configuration is injected.** Read settings through the `Settings` object
  passed in, never from `os.environ` inside domain code.

## Repository pattern

Repositories are the only code that queries PostgreSQL. They extend
`BaseRepository[Model]` in `repositories/base.py`.

- **One repository per aggregate**, named after it: `ApiKeyRepository`,
  `MemoryRepository`.
- **Tenant scope is explicit.** Every method that reads or writes tenant data
  takes an `organization_id` and filters on it. The only unscoped lookup is
  authentication (`ApiKeyRepository.get_by_hash`), which is how the
  organization is determined in the first place.
- **Flush, never commit.** The caller owns the unit of work, so several
  repository calls can commit or roll back together.
- **Soft deletes are hidden by default.** Soft-deletable models exclude
  deleted rows unless `include_deleted=True` is passed.
- **Pagination is bounded.** Lists take `limit` and `offset`, clamp `limit` to
  `max_limit`, and return the total for the response envelope.

```python
class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, include_revoked: bool = False
    ) -> Sequence[ApiKey]:
        """Newest first."""
        statement = (
            self.select(include_deleted=include_revoked)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.id.desc())
            .limit(self.max_limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
```

## Strict typing

| Language   | Configuration                                                          |
| ---------- | ---------------------------------------------------------------------- |
| Python     | `mypy --strict` with the Pydantic plugin; Ruff with bugbear and bandit |
| TypeScript | `strict`, `noUncheckedIndexedAccess`, `noImplicitOverride`             |

- Public functions have complete signatures. Prefer precise types (`Literal`,
  `TypedDict`, discriminated unions) over `str` and `dict`.
- Validate at the edges: Pydantic schemas on the server, Zod and Pydantic in
  the SDKs. Inside the edges, trust the types.
- Suppressions (`# type: ignore[code]`, `@ts-expect-error`) need a specific
  error code and a comment explaining why the checker cannot see the truth.
- Shared TypeScript contracts live in `packages/types` and are re-exported by
  the SDK. Change them together with the API schemas.

## Tests

Tests are required for every behavior change and run in CI on every pull
request.

| Suite          | Tooling                                    | Location                           |
| -------------- | ------------------------------------------ | ---------------------------------- |
| API            | pytest, pytest-asyncio                     | `apps/api/tests/`                  |
| Python SDK     | pytest                                     | `packages/sdk-py/tests/`           |
| TypeScript SDK | Vitest                                     | `packages/sdk-ts/tests/`           |
| Dashboard      | Vitest, Testing Library                    | `apps/dashboard/src/**/*.test.tsx` |
| Migrations     | Alembic                                    | CI job `tests / migrations`        |
| Infrastructure | unittest, shellcheck, actionlint, promtool | CI job `infra`                     |

- **Real dependencies.** Tests marked `database` or `redis` run against real
  PostgreSQL and Redis. Each test runs in a rolled-back transaction and uses a
  random Redis key prefix, so suites can run in parallel and never touch
  shared data.
- **Deterministic.** No network calls to model providers: tests use in-process
  fakes behind the provider interface, and the local embedding provider.
- **Behavior, not implementation.** Name tests after the behavior they pin
  (`test_a_key_cannot_reach_another_organization`) and assert on public
  results.
- **Warnings are errors.** Pytest runs with `filterwarnings = error`.

## Database changes

- Schema changes are Alembic migrations in `apps/api/migrations/versions/`,
  generated with `pnpm --filter @celestra/cortex-api db:revision -- -m "..."`
  and then reviewed by hand.
- Every migration downgrades. CI upgrades from empty, runs `alembic check`,
  downgrades to base, and upgrades again.
- Keys are UUIDv7, timestamps are `timestamptz`, and every foreign key is
  indexed.
- Migrations that rewrite large tables or change data semantics need an RFC
  with a rollout plan.

## API changes

- Follow [protocols.md](protocols.md): error envelope, pagination, headers,
  and streaming formats are uniform across endpoints.
- Within a version, changes are additive. Removing or renaming a field, or
  changing its meaning, is a breaking change and needs an RFC.
- Update, in the same pull request: the schemas, `packages/types`, both SDKs
  with tests, the API reference in `apps/api/README.md`, and the changelog
  entry through your commit message.

## Documentation

- Write for a capable engineer who has not seen the code. Lead with what the
  thing does, then how to use it, then how it works.
- Code comments explain constraints the code cannot show, not what the next
  line does.
- Prose uses complete sentences and plain language. No emojis.

## Review

Reviewers check, in order: correctness and tenant isolation, test coverage of
the change, adherence to the layers above, API compatibility, operability
(logs, metrics, failure modes), and documentation. Approval means the reviewer
would be comfortable operating the change in production.
