import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from cortex_api.services.router.base import (
    HEALTH_ERRORS,
    ModelSpec,
    Provider,
    ProviderError,
    ProviderName,
)


class RegistryError(LookupError):
    pass


class UnknownProviderError(RegistryError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Provider {name!r} is not registered")
        self.name = name


class UnknownModelError(RegistryError):
    def __init__(self, reference: str) -> None:
        super().__init__(f"Model {reference!r} is not in the catalog")
        self.reference = reference


class AmbiguousModelError(RegistryError):
    def __init__(self, reference: str, matches: list[ModelSpec]) -> None:
        keys = ", ".join(spec.key for spec in matches)
        super().__init__(f"Model {reference!r} is ambiguous; use one of: {keys}")
        self.reference = reference
        self.matches = matches


class AvailabilityReason(StrEnum):
    OK = "ok"
    NOT_CONFIGURED = "not_configured"
    CIRCUIT_OPEN = "circuit_open"


@dataclass(frozen=True, slots=True)
class Availability:
    available: bool
    reason: AvailabilityReason


@dataclass(slots=True)
class _ProviderHealth:
    consecutive_failures: int = 0
    open_until: float = 0.0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderHealthSnapshot:
    provider: ProviderName
    configured: bool
    circuit_open: bool
    consecutive_failures: int
    last_error: str | None


class ProviderRegistry:
    """Catalog of providers and models plus live health.

    Health is per process: a circuit breaker per provider (outages and bad
    credentials are provider-wide) and an EWMA of observed latency per model.
    After `failure_threshold` consecutive health failures the provider is
    skipped for `cooldown_seconds`; the next call after cooldown is a probe, and
    one more failure re-opens the circuit immediately.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 30.0,
        latency_smoothing: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if not 0 < latency_smoothing <= 1:
            raise ValueError("latency_smoothing must be in (0, 1]")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.latency_smoothing = latency_smoothing
        self._clock = clock
        self._lock = threading.Lock()
        self._providers: dict[ProviderName, Provider] = {}
        self._models: dict[str, ModelSpec] = {}
        self._health: dict[ProviderName, _ProviderHealth] = {}
        self._latency: dict[str, float] = {}

    # --- Catalog -----------------------------------------------------------

    def register(self, provider: Provider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"Provider {provider.name} is already registered")
        keys = [spec.key for spec in provider.models]
        duplicates = sorted(
            {k for k in keys if keys.count(k) > 1} | (set(keys) & set(self._models))
        )
        if duplicates:
            raise ValueError(f"Duplicate model keys: {duplicates}")
        self._providers[provider.name] = provider
        self._health[provider.name] = _ProviderHealth()
        for spec in provider.models:
            self._models[spec.key] = spec

    def provider(self, name: ProviderName | str) -> Provider:
        try:
            return self._providers[ProviderName(name)]
        except (KeyError, ValueError):
            raise UnknownProviderError(str(name)) from None

    def providers(self) -> list[Provider]:
        return list(self._providers.values())

    def models(self, provider: ProviderName | None = None) -> list[ModelSpec]:
        return [
            spec for spec in self._models.values() if provider is None or spec.provider is provider
        ]

    def resolve(self, reference: str, *, provider: ProviderName | None = None) -> ModelSpec:
        """Find a model by `provider/id`, bare id, or alias (case-insensitive)."""
        ref = reference.strip().lower()
        if "/" in ref:
            prefix, _, ref = ref.partition("/")
            try:
                prefixed = ProviderName(prefix)
            except ValueError:
                raise UnknownModelError(reference) from None
            if provider is not None and provider is not prefixed:
                raise UnknownModelError(reference)
            provider = prefixed

        matches = [
            spec
            for spec in self._models.values()
            if (provider is None or spec.provider is provider)
            and (spec.id.lower() == ref or ref in (alias.lower() for alias in spec.aliases))
        ]
        if not matches:
            raise UnknownModelError(reference)
        if len(matches) > 1:
            raise AmbiguousModelError(reference, matches)
        return matches[0]

    # --- Health --------------------------------------------------------------

    def availability(self, spec: ModelSpec) -> Availability:
        if not self.provider(spec.provider).configured:
            return Availability(False, AvailabilityReason.NOT_CONFIGURED)
        with self._lock:
            open_until = self._health[spec.provider].open_until
        if open_until > self._clock():
            return Availability(False, AvailabilityReason.CIRCUIT_OPEN)
        return Availability(True, AvailabilityReason.OK)

    def is_available(self, spec: ModelSpec) -> bool:
        return self.availability(spec).available

    def expected_latency_ms(self, spec: ModelSpec) -> float:
        with self._lock:
            return self._latency.get(spec.key, float(spec.typical_latency_ms))

    def record_success(self, spec: ModelSpec, latency_ms: float) -> None:
        with self._lock:
            health = self._health[spec.provider]
            health.consecutive_failures = 0
            health.open_until = 0.0
            previous = self._latency.get(spec.key)
            self._latency[spec.key] = (
                latency_ms
                if previous is None
                else previous + self.latency_smoothing * (latency_ms - previous)
            )

    def record_failure(self, spec: ModelSpec, error: ProviderError) -> None:
        if error.kind not in HEALTH_ERRORS:
            return
        with self._lock:
            health = self._health[spec.provider]
            health.consecutive_failures += 1
            health.last_error = error.describe()
            if health.consecutive_failures >= self.failure_threshold:
                health.open_until = self._clock() + self.cooldown_seconds

    def health(self, name: ProviderName) -> ProviderHealthSnapshot:
        provider = self.provider(name)
        with self._lock:
            health = self._health[name]
            return ProviderHealthSnapshot(
                provider=name,
                configured=provider.configured,
                circuit_open=health.open_until > self._clock(),
                consecutive_failures=health.consecutive_failures,
                last_error=health.last_error,
            )
