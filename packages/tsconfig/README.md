# @celestra/cortex-tsconfig

Shared TypeScript configurations. Lives in a workspace package (rather than a
root file) so `turbo prune` includes it in pruned Docker build contexts.

- `base.json` — strict defaults for libraries.
- `nextjs.json` — Next.js apps.

```json
{ "extends": "@celestra/cortex-tsconfig/base.json" }
```
