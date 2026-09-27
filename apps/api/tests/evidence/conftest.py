# Fake providers, so completions in this suite produce real decisions.
from ..router.conftest import (  # noqa: F401
    clock,
    cortex_router,
    providers,
    registry,
    router_override,
    sleeps,
)
