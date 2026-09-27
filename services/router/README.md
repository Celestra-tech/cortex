# cortex-router

Selects the model and provider that should serve each request, balancing
capability, latency, cost, and availability.

**Status:** the implementation ships inside the API at `apps/api/src/cortex_api/services/router/`. This package is the boundary it moves to once it needs to scale or deploy independently of the gateway; see [ARCHITECTURE.md](../../ARCHITECTURE.md).

Package: `cortex_router` · uv workspace member · Python 3.13
