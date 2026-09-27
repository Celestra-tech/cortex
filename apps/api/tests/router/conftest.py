import pytest

from cortex_api.services.router.base import ProviderName
from cortex_api.services.router.fallback import FallbackExecutor
from cortex_api.services.router.policies import RoutingPolicy
from cortex_api.services.router.registry import ProviderRegistry
from cortex_api.services.router.router import CortexRouter

from .fakes import CATALOGS, FakeClock, FakeProvider, SleepRecorder


@pytest.fixture
def providers() -> dict[ProviderName, FakeProvider]:
    return {name: FakeProvider(name, models) for name, models in CATALOGS.items()}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sleeps() -> SleepRecorder:
    return SleepRecorder()


@pytest.fixture
def registry(providers: dict[ProviderName, FakeProvider], clock: FakeClock) -> ProviderRegistry:
    registry = ProviderRegistry(failure_threshold=3, cooldown_seconds=30.0, clock=clock)
    for provider in providers.values():
        registry.register(provider)
    return registry


@pytest.fixture
def cortex_router(registry: ProviderRegistry, sleeps: SleepRecorder) -> CortexRouter:
    return CortexRouter(
        registry,
        RoutingPolicy(registry),
        FallbackExecutor(registry, max_retries=1, sleep=sleeps),
    )


@pytest.fixture
def router_override(cortex_router: CortexRouter) -> CortexRouter:
    return cortex_router
