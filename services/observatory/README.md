# cortex-observatory

Tracing, metrics, usage metering, and cost analytics across every request that
flows through Cortex.

**Status:** the implementation ships inside the API at `apps/api/src/cortex_api/services/observatory/`. This package is the boundary it moves to once it needs to scale or deploy independently of the gateway; see [ARCHITECTURE.md](../../ARCHITECTURE.md).

Package: `cortex_observatory` · uv workspace member · Python 3.13
