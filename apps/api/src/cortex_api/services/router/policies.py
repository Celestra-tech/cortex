import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cortex_api.models.model_execution import RoutingMode
from cortex_api.services.router.base import Capability, ModelSpec, ProviderName
from cortex_api.services.router.registry import ProviderRegistry

DEFAULT_FALLBACK_ORDER: tuple[ProviderName, ...] = (
    ProviderName.OPENAI,
    ProviderName.ANTHROPIC,
    ProviderName.GEMINI,
)


class Objective(StrEnum):
    BALANCED = "balanced"
    QUALITY = "quality"
    SPEED = "speed"
    COST = "cost"


@dataclass(frozen=True, slots=True)
class Weights:
    quality: float
    latency: float
    cost: float


OBJECTIVE_WEIGHTS: Mapping[Objective, Weights] = {
    Objective.BALANCED: Weights(quality=0.50, latency=0.25, cost=0.25),
    Objective.QUALITY: Weights(quality=0.70, latency=0.15, cost=0.15),
    Objective.SPEED: Weights(quality=0.20, latency=0.60, cost=0.20),
    Objective.COST: Weights(quality=0.20, latency=0.20, cost=0.60),
}


# --- Errors ------------------------------------------------------------------


class RoutingError(Exception):
    """Base for failures decided before any provider is called."""

    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PolicyViolationError(RoutingError):
    status_code = 403


class InvalidPolicyError(RoutingError):
    status_code = 500


class NoRouteError(RoutingError):
    status_code = 503

    def __init__(self, message: str, rejections: Mapping[str, str] | None = None) -> None:
        super().__init__(message)
        self.rejections = dict(rejections or {})


# --- Organization policy -------------------------------------------------------


class OrganizationPolicy(BaseModel):
    """Tenant routing rules, stored at `organizations.settings["routing"]`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_providers: list[ProviderName] | None = Field(
        default=None, description="If set, only these providers are ever called."
    )
    blocked_models: list[str] = Field(
        default_factory=list, description="Model keys (`provider/id`) or bare ids."
    )
    default_objective: Objective = Objective.BALANCED
    allow_fallback: bool = True
    fallback_order: list[ProviderName] | None = None
    max_cost_per_request_usd: Decimal | None = Field(default=None, gt=0)

    @classmethod
    def from_organization_settings(cls, settings: Mapping[str, Any] | None) -> "OrganizationPolicy":
        # Fail closed: silently ignoring a broken policy could send data to a
        # provider the tenant has excluded.
        try:
            return cls.model_validate((settings or {}).get("routing") or {})
        except ValidationError as exc:
            raise InvalidPolicyError(f"Organization routing policy is invalid: {exc}") from exc

    def rejection(self, spec: ModelSpec) -> str | None:
        if self.allowed_providers is not None and spec.provider not in self.allowed_providers:
            return f"provider {spec.provider} is not allowed by organization policy"
        blocked = {entry.lower() for entry in self.blocked_models}
        if spec.key.lower() in blocked or spec.id.lower() in blocked:
            return "model is blocked by organization policy"
        return None


# --- Plans -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RoutingRequirements:
    prompt_tokens: int
    max_tokens: int | None = None
    capabilities: frozenset[Capability] = frozenset()

    def output_budget(self, spec: ModelSpec, default_max_tokens: int) -> int:
        return self.max_tokens or min(default_max_tokens, spec.max_output_tokens)


@dataclass(frozen=True, slots=True)
class Candidate:
    spec: ModelSpec
    score: float
    expected_latency_ms: float
    max_cost: Decimal
    """Upper bound: full prompt plus the whole output budget."""
    max_tokens: int


@dataclass(frozen=True, slots=True)
class RoutingPlan:
    mode: RoutingMode
    objective: Objective
    candidates: tuple[Candidate, ...]
    """Primary first, then fallbacks in order."""
    reason: str

    @property
    def primary(self) -> Candidate:
        return self.candidates[0]


class RoutingPolicy:
    """Turns a request plus live registry state into an ordered `RoutingPlan`.

    AUTO      best-scoring eligible model for the objective.
    PREFERRED the requested model (or best model of the requested provider);
              reroutes like AUTO if it is unavailable.
    STRICT    the requested model only: no reroute, no fallback.

    Fallbacks (AUTO and PREFERRED) take the best eligible model from each other
    provider in `fallback_order`, so one provider outage costs one attempt.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        fallback_order: Sequence[ProviderName] = DEFAULT_FALLBACK_ORDER,
        default_max_tokens: int = 1024,
    ) -> None:
        self.registry = registry
        self.fallback_order = tuple(fallback_order)
        self.default_max_tokens = default_max_tokens

    def plan(
        self,
        requirements: RoutingRequirements,
        *,
        mode: RoutingMode,
        objective: Objective,
        policy: OrganizationPolicy,
        model: ModelSpec | None = None,
        provider: ProviderName | None = None,
        allow_fallback: bool = True,
    ) -> RoutingPlan:
        if mode is not RoutingMode.AUTO and model is None and provider is None:
            raise RoutingError(f"{mode} routing requires a model or provider")
        if mode is RoutingMode.STRICT and model is None:
            raise RoutingError("strict routing requires a model")

        rejections: dict[str, str] = {}
        eligible: list[ModelSpec] = []
        for spec in self.registry.models():
            reason = policy.rejection(spec) or self._technical_rejection(spec, requirements, policy)
            if reason:
                rejections[spec.key] = reason
            else:
                eligible.append(spec)
        ranked = self._rank(eligible, requirements, objective)

        if model is not None and (violation := policy.rejection(model)):
            raise PolicyViolationError(f"{model.key}: {violation}")
        allowed = policy.allowed_providers
        if provider is not None and allowed is not None and provider not in allowed:
            raise PolicyViolationError(f"provider {provider} is not allowed by organization policy")

        if mode is RoutingMode.STRICT and model is not None:
            candidate = _find(ranked, model)
            if candidate is None:
                raise NoRouteError(
                    f"strict routing: {model.key} is unavailable ({rejections[model.key]})",
                    {model.key: rejections[model.key]},
                )
            return RoutingPlan(mode, objective, (candidate,), f"strict: {model.key} as requested")

        if not ranked:
            raise NoRouteError("no eligible model for this request", rejections)

        if mode is RoutingMode.PREFERRED:
            if model is not None:
                requested, primary = model.key, _find(ranked, model)
                unavailable = rejections.get(model.key, "")
            else:
                requested = str(provider)
                primary = next((c for c in ranked if c.spec.provider is provider), None)
                unavailable = f"no eligible {provider} model"
            if primary is not None:
                reason = f"preferred: {primary.spec.key} as requested"
            else:
                primary = ranked[0]
                reason = (
                    f"preferred: {requested} unavailable ({unavailable}); "
                    f"routed to {primary.spec.key} (best {objective} score {primary.score:.2f})"
                )
        else:
            primary = ranked[0]
            runner_up = (
                f"; next {ranked[1].spec.key} {ranked[1].score:.2f}" if len(ranked) > 1 else ""
            )
            reason = (
                f"auto: {primary.spec.key} scored {primary.score:.2f} for objective={objective} "
                f"among {len(ranked)} eligible models{runner_up}"
            )

        fallbacks: list[Candidate] = []
        if allow_fallback and policy.allow_fallback:
            order = tuple(policy.fallback_order or self.fallback_order)
            fallbacks = _fallback_chain(primary, ranked, order)
        return RoutingPlan(mode, objective, (primary, *fallbacks), reason)

    def _technical_rejection(
        self, spec: ModelSpec, requirements: RoutingRequirements, policy: OrganizationPolicy
    ) -> str | None:
        availability = self.registry.availability(spec)
        if not availability.available:
            return f"provider {availability.reason}"
        missing = requirements.capabilities - spec.capabilities
        if missing:
            return f"missing capabilities: {', '.join(sorted(missing))}"
        if requirements.max_tokens and requirements.max_tokens > spec.max_output_tokens:
            return (
                f"max_tokens {requirements.max_tokens} exceeds model limit {spec.max_output_tokens}"
            )
        budget = requirements.output_budget(spec, self.default_max_tokens)
        if requirements.prompt_tokens + budget > spec.context_window:
            return (
                f"~{requirements.prompt_tokens} prompt + {budget} output tokens exceed "
                f"context window {spec.context_window}"
            )
        limit = policy.max_cost_per_request_usd
        if limit is not None:
            cost = spec.estimate_cost(requirements.prompt_tokens, budget)
            if cost > limit:
                return f"estimated cost ${cost} exceeds policy limit ${limit}"
        return None

    def _rank(
        self, specs: list[ModelSpec], requirements: RoutingRequirements, objective: Objective
    ) -> list[Candidate]:
        if not specs:
            return []
        weights = OBJECTIVE_WEIGHTS[objective]
        budgets = [requirements.output_budget(s, self.default_max_tokens) for s in specs]
        latencies = [self.registry.expected_latency_ms(s) for s in specs]
        costs = [
            s.estimate_cost(requirements.prompt_tokens, b)
            for s, b in zip(specs, budgets, strict=True)
        ]
        latency_scale = _LogScale([max(1.0, v) for v in latencies])
        cost_scale = _LogScale([max(1e-9, float(c)) for c in costs])

        candidates = [
            Candidate(
                spec=spec,
                score=round(
                    weights.quality * (spec.quality - 1) / 4
                    + weights.latency * (1 - latency_scale.normalize(max(1.0, latency)))
                    + weights.cost * (1 - cost_scale.normalize(max(1e-9, float(cost)))),
                    6,
                ),
                expected_latency_ms=latency,
                max_cost=cost,
                max_tokens=budget,
            )
            for spec, budget, latency, cost in zip(specs, budgets, latencies, costs, strict=True)
        ]
        order = {name: index for index, name in enumerate(self.fallback_order)}
        candidates.sort(
            key=lambda c: (-c.score, order.get(c.spec.provider, len(order)), c.spec.key)
        )
        return candidates


class _LogScale:
    """Maps values onto [0, 1] by log position, so ratios matter rather than differences."""

    def __init__(self, values: list[float]) -> None:
        logs = [math.log(v) for v in values]
        self.low, self.high = min(logs), max(logs)

    def normalize(self, value: float) -> float:
        if self.high <= self.low:
            return 0.0
        return (math.log(value) - self.low) / (self.high - self.low)


def _find(ranked: list[Candidate], spec: ModelSpec) -> Candidate | None:
    return next((c for c in ranked if c.spec.key == spec.key), None)


def _fallback_chain(
    primary: Candidate, ranked: list[Candidate], order: tuple[ProviderName, ...]
) -> list[Candidate]:
    used = {primary.spec.provider}
    providers = [*order, *(c.spec.provider for c in ranked if c.spec.provider not in order)]
    chain: list[Candidate] = []
    for provider in providers:
        if provider in used:
            continue
        best = next((c for c in ranked if c.spec.provider is provider), None)
        if best is not None:
            chain.append(best)
            used.add(provider)
    return chain
