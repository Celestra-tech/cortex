<!--
Title: a Conventional Commit, e.g. "feat(router): add a latency ceiling to routing policy".
It becomes the squash commit and the release note.
-->

## Summary

What this change does and why. Link the issue it resolves and, for major
features, the accepted RFC.

Closes #

## Changes

-

## Testing

How the change is verified. List new or updated tests, and any manual checks
with their results.

## Checklist

- [ ] The title is a Conventional Commit; breaking changes use `!` and explain the migration below.
- [ ] Tests cover the change and fail without it.
- [ ] `pnpm turbo run lint typecheck test build` and `pnpm format:check` pass locally.
- [ ] Layering holds: routes call services, services call repositories, vendor code stays in adapters.
- [ ] Types are strict; no new `Any`, `# type: ignore`, or `@ts-ignore` without a stated reason.
- [ ] API changes update the schemas, `packages/types`, both SDKs, and the API reference together.
- [ ] Schema changes include an Alembic migration that upgrades and downgrades.
- [ ] Documentation is updated where behavior changed.

## Breaking changes and migration

None.
