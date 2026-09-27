# Contributing to CELESTRA Cortex

Thank you for helping build Cortex. This guide covers how to propose changes
and what a change needs before it merges. The engineering standards are
explained in depth, with examples from the codebase, in
[docs/contributing.md](docs/contributing.md).

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues are never reported in public; see [SECURITY.md](SECURITY.md).

## Ways to contribute

- **Report a bug** with the bug report template. Include versions, steps to
  reproduce, and the `X-Request-ID` of a failing call when you have one.
- **Propose a feature** with the feature request template. Major features
  need an [RFC](#rfcs) first.
- **Improve documentation.** Small fixes can go straight to a pull request.
- **Pick up an issue** labeled `good first issue` or `help wanted`. Comment
  first so work is not duplicated.

## Development setup

Prerequisites: Node 22 (`.nvmrc`), pnpm 9.15, [uv](https://docs.astral.sh/uv/),
and Docker for PostgreSQL and Redis.

```bash
git clone https://github.com/Celestra-tech/cortex.git
cd cortex
cp .env.example .env
pnpm install
uv sync --all-packages
docker compose up -d postgres redis
pnpm dev
```

Database and Redis tests run when their URLs are set:

```bash
export CORTEX_TEST_DATABASE_URL=postgresql+asyncpg://cortex:cortex@localhost:5432/celestra_cortex_test
export CORTEX_TEST_REDIS_URL=redis://localhost:6379/15
pnpm test
```

See [docs/development.md](docs/development.md) for migrations and single-app
workflows.

## Engineering standards

Every change is held to five standards. Reviewers apply them to every pull
request.

1. **Clean Architecture.** Dependencies point inward: routes call services,
   services call repositories, and domain code never imports FastAPI, a vendor
   SDK, or a wire format. Provider-specific code stays in its adapter.
2. **Repository pattern.** Repositories are the only code that touches the
   database. They scope every query to an organization, flush but never
   commit, and return models, not rows. The caller owns the transaction.
3. **Strict typing.** `mypy --strict` for Python and `strict` TypeScript with
   `noUncheckedIndexedAccess`. No `Any`, `# type: ignore`, `as any`, or
   `@ts-ignore` without a comment explaining the constraint.
4. **Tests are required.** Every behavior change comes with tests that fail
   without it. Bug fixes start with a failing regression test. Database and
   Redis code is tested against real PostgreSQL and Redis, not mocks.
5. **RFC before major features.** New subsystems, public API changes, schema
   changes that alter data semantics, and new dependencies with a large
   footprint start as an RFC.

## Pull requests

1. Fork the repository and branch from `main`.
2. Keep each pull request to one logical change. Split refactors from
   behavior changes.
3. Run the checks locally:

   ```bash
   pnpm turbo run lint typecheck test build
   pnpm format:check
   ```

4. Update documentation, SDKs, and `packages/types` together with any API
   change. Both SDKs must expose the same surface.
5. Add database changes as an Alembic migration that upgrades and downgrades
   cleanly (`alembic check` must pass).
6. Open the pull request with the template filled in. CI must be green and one
   maintainer must approve before merge. `main` uses squash merges and a linear
   history.

### Commit messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/).
Release notes and version bumps are generated from them.

```
feat(router): add a latency ceiling to routing policy
fix(memory): rebuild the session window when Redis evicts it
docs(protocols): document WebSocket close codes
```

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`,
`chore`. Scopes are component names: `api`, `router`, `memory`, `knowledge`,
`observatory`, `auth`, `sdk-ts`, `sdk-py`, `dashboard`, `infra`. A breaking
change adds `!` after the scope and a `BREAKING CHANGE:` footer.

## RFCs

An RFC is a short design document that settles the approach before code is
written. Open an issue titled `RFC: <title>` with the `rfc` label and these
sections:

- **Summary.** One paragraph.
- **Motivation.** The problem, who has it, and evidence.
- **Design.** Public API, data model, and failure modes. Include request and
  response examples for API changes.
- **Alternatives.** What else was considered and why it lost.
- **Rollout.** Migrations, compatibility, and how the change is observed in
  production.
- **Open questions.**

An RFC is accepted when a maintainer approves it after at least five business
days of public discussion. Implementation pull requests link to the accepted
RFC.

## Licensing

Cortex is licensed under the [Apache License 2.0](LICENSE). By submitting a
contribution you agree that it is licensed under the same terms, as described
in section 5 of the license. You must have the right to submit the work.
