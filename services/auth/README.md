# cortex-auth

Identity, tenancy, API keys, and role-based access control for every Cortex
surface.

**Status:** the implementation ships inside the API at `apps/api/src/cortex_api/services/api_keys.py`, `core/security.py`, and `api/deps.py`. This package is the boundary it moves to once it needs to scale or deploy independently of the gateway; see [ARCHITECTURE.md](../../ARCHITECTURE.md).

Package: `cortex_auth` · uv workspace member · Python 3.13
