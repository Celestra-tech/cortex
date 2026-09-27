import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Self

import httpx2
from pydantic import SecretStr

from cortex_api.core.config import Settings
from cortex_api.database.ids import uuid7
from cortex_api.models.model_execution import RoutingMode
from cortex_api.services.memory.encoder import HeuristicTokenEstimator, TokenEstimator
from cortex_api.services.router.base import (
    Capability,
    ChatMessage,
    HTTPProviderConfig,
    ModelSpec,
    ProviderName,
    ProviderRequest,
    ProviderResponse,
)
from cortex_api.services.router.fallback import (
    AllProvidersFailedError,
    Attempt,
    ExecutionResult,
    FallbackExecutor,
)
from cortex_api.services.router.policies import (
    Candidate,
    Objective,
    OrganizationPolicy,
    RoutingPlan,
    RoutingPolicy,
    RoutingRequirements,
)
from cortex_api.services.router.providers.anthropic_provider import AnthropicProvider
from cortex_api.services.router.providers.gemini_provider import GeminiProvider
from cortex_api.services.router.providers.openai_provider import OpenAIProvider
from cortex_api.services.router.registry import ProviderRegistry

MESSAGE_OVERHEAD_TOKENS = 4
"""Per-message framing tokens added by chat templates."""


@dataclass(frozen=True, slots=True)
class CompletionTask:
    messages: tuple[ChatMessage, ...]
    policy: OrganizationPolicy = field(default_factory=OrganizationPolicy)
    model: str | None = None
    provider: ProviderName | None = None
    mode: RoutingMode | None = None
    objective: Objective | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    capabilities: frozenset[Capability] = frozenset()
    allow_fallback: bool = True


@dataclass(frozen=True, slots=True)
class RoutedCompletion:
    id: uuid.UUID
    created_at: datetime
    plan: RoutingPlan
    result: ExecutionResult
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_estimate: Decimal

    @property
    def spec(self) -> ModelSpec:
        return self.result.candidate.spec

    @property
    def response(self) -> ProviderResponse:
        return self.result.response

    @property
    def attempts(self) -> tuple[Attempt, ...]:
        return self.result.attempts

    @property
    def routing_reason(self) -> str:
        """The plan's reason, plus the fallback taken when the primary did not answer."""
        primary = self.plan.primary.spec
        if self.spec.key == primary.key:
            return self.plan.reason
        failures = ", ".join(
            dict.fromkeys(
                f"{attempt.spec.key} {attempt.error.kind}"
                for attempt in self.attempts
                if attempt.error is not None
            )
        )
        return f"{self.plan.reason}; fell back to {self.spec.key} after {failures}"


class CompletionFailedError(AllProvidersFailedError):
    """Every planned provider failed. Carries the plan so failures can be recorded."""

    def __init__(self, completion_id: uuid.UUID, plan: RoutingPlan, attempts: tuple[Attempt, ...]):
        super().__init__(attempts)
        self.completion_id = completion_id
        self.plan = plan


class CortexRouter:
    def __init__(
        self,
        registry: ProviderRegistry,
        policy: RoutingPolicy,
        executor: FallbackExecutor,
        *,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.executor = executor
        self.estimator = estimator or HeuristicTokenEstimator()

    @classmethod
    def from_settings(cls, settings: Settings, client: httpx2.AsyncClient) -> Self:
        registry = build_registry(settings, client)
        return cls(
            registry,
            RoutingPolicy(
                registry,
                fallback_order=[ProviderName(name) for name in settings.router_fallback_order],
                default_max_tokens=settings.router_default_max_tokens,
            ),
            FallbackExecutor(registry, max_retries=settings.router_max_retries),
        )

    def estimate_prompt_tokens(self, messages: Sequence[ChatMessage]) -> int:
        return sum(self.estimator.count(m.content) + MESSAGE_OVERHEAD_TOKENS for m in messages)

    def plan(self, task: CompletionTask) -> RoutingPlan:
        model = self.registry.resolve(task.model, provider=task.provider) if task.model else None
        mode = task.mode or (RoutingMode.PREFERRED if model or task.provider else RoutingMode.AUTO)
        return self.policy.plan(
            RoutingRequirements(
                prompt_tokens=self.estimate_prompt_tokens(task.messages),
                max_tokens=task.max_tokens,
                capabilities=task.capabilities,
            ),
            mode=mode,
            objective=task.objective or task.policy.default_objective,
            policy=task.policy,
            model=model,
            provider=task.provider or (model.provider if model else None),
            allow_fallback=task.allow_fallback,
        )

    async def complete(self, task: CompletionTask) -> RoutedCompletion:
        completion_id = uuid7()
        created_at = datetime.now(UTC)
        plan = self.plan(task)

        def build(candidate: Candidate) -> ProviderRequest:
            return ProviderRequest(
                model=candidate.spec,
                messages=task.messages,
                max_tokens=candidate.max_tokens,
                temperature=task.temperature,
            )

        started = time.perf_counter()
        try:
            result = await self.executor.execute(plan, build)
        except AllProvidersFailedError as exc:
            raise CompletionFailedError(completion_id, plan, exc.attempts) from exc

        response = result.response
        prompt_tokens = response.prompt_tokens or self.estimate_prompt_tokens(task.messages)
        completion_tokens = response.completion_tokens or self.estimator.count(response.output)
        return RoutedCompletion(
            id=completion_id,
            created_at=created_at,
            plan=plan,
            result=result,
            latency_ms=round((time.perf_counter() - started) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_estimate=result.candidate.spec.estimate_cost(prompt_tokens, completion_tokens),
        )


def build_registry(settings: Settings, client: httpx2.AsyncClient) -> ProviderRegistry:
    """Register every built-in provider. Ones without credentials show as unavailable."""
    registry = ProviderRegistry(
        failure_threshold=settings.router_circuit_failure_threshold,
        cooldown_seconds=settings.router_circuit_cooldown_seconds,
    )

    def config(key: SecretStr | None, base_url: str) -> HTTPProviderConfig:
        return HTTPProviderConfig(
            api_key=(key.get_secret_value() if key else None) or None,
            base_url=base_url,
            timeout_seconds=settings.router_timeout_seconds,
        )

    registry.register(
        OpenAIProvider(
            client=client, config=config(settings.openai_api_key, settings.openai_base_url)
        )
    )
    registry.register(
        AnthropicProvider(
            client=client, config=config(settings.anthropic_api_key, settings.anthropic_base_url)
        )
    )
    registry.register(
        GeminiProvider(
            client=client, config=config(settings.gemini_api_key, settings.gemini_base_url)
        )
    )
    return registry
