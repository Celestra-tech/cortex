import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cortex_api.services.router.base import (
    ModelSpec,
    ProviderError,
    ProviderErrorKind,
    ProviderRequest,
    ProviderResponse,
)
from cortex_api.services.router.policies import Candidate, RoutingPlan
from cortex_api.services.router.registry import ProviderRegistry
from cortex_api.services.router.telemetry import (
    provider_span,
    record_circuit,
    record_failure,
    record_skip,
    record_success,
)

logger = logging.getLogger(__name__)

RequestBuilder = Callable[[Candidate], ProviderRequest]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Attempt:
    number: int
    """1-based position across the whole completion, including retries."""
    candidate: Candidate
    is_fallback: bool
    latency_ms: int
    response: ProviderResponse | None = None
    error: ProviderError | None = None

    @property
    def spec(self) -> ModelSpec:
        return self.candidate.spec

    @property
    def success(self) -> bool:
        return self.response is not None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    response: ProviderResponse
    candidate: Candidate
    attempts: tuple[Attempt, ...]


class AllProvidersFailedError(Exception):
    status_code = 502

    def __init__(self, attempts: tuple[Attempt, ...]) -> None:
        tried = "; ".join(
            f"{a.spec.key}: {a.error.describe()}" for a in attempts if a.error is not None
        )
        super().__init__(f"All providers failed ({tried or 'no provider could be tried'})")
        self.attempts = attempts


class FallbackExecutor:
    """Runs a plan: retry transient errors in place, then move down the chain.

    Candidates whose circuit opened mid-request (for example because an
    earlier attempt tripped it) are skipped without a call.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        max_retries: int = 1,
        backoff_base_seconds: float = 0.25,
        backoff_max_seconds: float = 4.0,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.registry = registry
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._sleep = sleep
        self._clock = clock

    async def execute(self, plan: RoutingPlan, build_request: RequestBuilder) -> ExecutionResult:
        attempts: list[Attempt] = []
        for index, candidate in enumerate(plan.candidates):
            if index > 0 and not self.registry.is_available(candidate.spec):
                logger.info("Skipping %s: provider became unavailable", candidate.spec.key)
                record_skip(candidate.spec)
                continue
            provider = self.registry.provider(candidate.spec.provider)
            request = build_request(candidate)

            for retry in range(self.max_retries + 1):
                started = self._clock()
                with provider_span(
                    candidate.spec, attempt=len(attempts) + 1, fallback=index > 0
                ) as span:
                    try:
                        response = await provider.complete(request)
                    except ProviderError as exc:
                        error = exc
                    except Exception as exc:  # A bug in one adapter must not sink the chain.
                        logger.exception("Provider %s raised unexpectedly", provider.name)
                        error = ProviderError(
                            provider.name,
                            ProviderErrorKind.INVALID_RESPONSE,
                            f"unexpected {type(exc).__name__}: {exc}",
                        )
                    else:
                        latency_ms = self._elapsed_ms(started)
                        self.registry.record_success(candidate.spec, latency_ms)
                        record_success(span, candidate.spec, response, latency_ms)
                        record_circuit(self.registry, candidate.spec.provider)
                        attempts.append(
                            Attempt(len(attempts) + 1, candidate, index > 0, latency_ms, response)
                        )
                        return ExecutionResult(response, candidate, tuple(attempts))

                    latency_ms = self._elapsed_ms(started)
                    self.registry.record_failure(candidate.spec, error)
                    record_failure(span, candidate.spec, error, latency_ms)
                    record_circuit(self.registry, candidate.spec.provider)
                attempts.append(
                    Attempt(len(attempts) + 1, candidate, index > 0, latency_ms, error=error)
                )
                logger.warning(
                    "Attempt %d on %s failed: %s",
                    len(attempts),
                    candidate.spec.key,
                    error.describe(),
                )
                if (
                    not error.retryable
                    or retry == self.max_retries
                    or not self.registry.is_available(candidate.spec)
                ):
                    break
                await self._sleep(self._backoff(retry, error))

        raise AllProvidersFailedError(tuple(attempts))

    def _backoff(self, retry: int, error: ProviderError) -> float:
        if error.retry_after_seconds is not None:
            return min(error.retry_after_seconds, self.backoff_max_seconds)
        ceiling = min(self.backoff_max_seconds, self.backoff_base_seconds * 2**retry)
        return random.uniform(ceiling / 2, ceiling)  # noqa: S311 - jitter, not security

    def _elapsed_ms(self, started: float) -> int:
        return max(0, round((self._clock() - started) * 1000))
