# cortex-memory

Session and long-term memory for agents: storage, recall, summarisation, and
expiry of conversational and task state.

**Status:** the implementation ships inside the API at `apps/api/src/cortex_api/services/memory/`. This package is the boundary it moves to once it needs to scale or deploy independently of the gateway; see [ARCHITECTURE.md](../../ARCHITECTURE.md).

Package: `cortex_memory` · uv workspace member · Python 3.13
