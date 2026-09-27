from decimal import Decimal
from typing import Any

import pytest

from cortex_api.models.message import MessageRole
from cortex_api.models.model_execution import RoutingMode
from cortex_api.services.router.base import (
    Capability,
    ChatMessage,
    ModelSpec,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
)
from cortex_api.services.router.policies import (
    InvalidPolicyError,
    NoRouteError,
    Objective,
    OrganizationPolicy,
    PolicyViolationError,
    RoutingError,
    RoutingPlan,
    RoutingPolicy,
    RoutingRequirements,
)
from cortex_api.services.router.registry import ProviderRegistry, UnknownModelError
from cortex_api.services.router.router import CompletionTask, CortexRouter

from .fakes import FakeProvider

DEFAULT = RoutingRequirements(prompt_tokens=50)


def plan(
    registry: ProviderRegistry,
    requirements: RoutingRequirements = DEFAULT,
    *,
    mode: RoutingMode = RoutingMode.AUTO,
    objective: Objective = Objective.BALANCED,
    policy: OrganizationPolicy | None = None,
    model: str | None = None,
    provider: ProviderName | None = None,
    **kwargs: Any,
) -> RoutingPlan:
    spec = registry.resolve(model) if model else None
    return RoutingPolicy(registry).plan(
        requirements,
        mode=mode,
        objective=objective,
        policy=policy or OrganizationPolicy(),
        model=spec,
        provider=provider or (spec.provider if spec else None),
        **kwargs,
    )


def keys(result: RoutingPlan) -> list[str]:
    return [c.spec.key for c in result.candidates]


def model(
    model_id: str, provider: ProviderName, *, quality: int, latency: int, out: str
) -> ModelSpec:
    return ModelSpec(
        id=model_id,
        provider=provider,
        display_name=model_id,
        context_window=128_000,
        max_output_tokens=8_192,
        input_cost_per_mtok=Decimal(out) / 4,
        output_cost_per_mtok=Decimal(out),
        quality=quality,
        typical_latency_ms=latency,
    )


@pytest.fixture
def synthetic() -> ProviderRegistry:
    """Three models with hand-checkable trade-offs."""
    registry = ProviderRegistry()
    registry.register(
        FakeProvider(
            ProviderName.OPENAI,
            [model("fast", ProviderName.OPENAI, quality=2, latency=500, out="0.20")],
        )
    )
    registry.register(
        FakeProvider(
            ProviderName.ANTHROPIC,
            [model("smart", ProviderName.ANTHROPIC, quality=5, latency=3_000, out="15")],
        )
    )
    registry.register(
        FakeProvider(
            ProviderName.GEMINI,
            [model("mid", ProviderName.GEMINI, quality=3, latency=1_000, out="1.50")],
        )
    )
    return registry


# --- AUTO ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("objective", "expected"),
    [
        (Objective.QUALITY, "anthropic/smart"),
        (Objective.SPEED, "openai/fast"),
        (Objective.COST, "openai/fast"),
    ],
)
def test_auto_picks_by_objective(
    synthetic: ProviderRegistry, objective: Objective, expected: str
) -> None:
    result = plan(synthetic, objective=objective)
    assert result.mode is RoutingMode.AUTO
    assert result.primary.spec.key == expected
    assert result.reason.startswith(f"auto: {expected} scored")
    scores = [c.score for c in result.candidates]
    assert scores[0] == max(scores)


def test_auto_fallback_chain_follows_provider_order(synthetic: ProviderRegistry) -> None:
    result = plan(synthetic, objective=Objective.QUALITY)
    assert keys(result) == ["anthropic/smart", "openai/fast", "gemini/mid"]

    reordered = plan(
        synthetic,
        objective=Objective.QUALITY,
        policy=OrganizationPolicy(fallback_order=[ProviderName.GEMINI, ProviderName.OPENAI]),
    )
    assert keys(reordered) == ["anthropic/smart", "gemini/mid", "openai/fast"]


def test_auto_on_real_catalog_uses_one_model_per_provider(registry: ProviderRegistry) -> None:
    result = plan(registry, objective=Objective.COST)
    assert result.primary.spec.key == "gemini/gemini-2.5-flash-lite"
    assert [c.spec.provider for c in result.candidates] == [
        ProviderName.GEMINI,
        ProviderName.OPENAI,
        ProviderName.ANTHROPIC,
    ]


def test_observed_latency_changes_the_choice(registry: ProviderRegistry) -> None:
    assert plan(registry, objective=Objective.SPEED).primary.spec.id == "gemini-2.5-flash-lite"
    registry.record_success(registry.resolve("gemini-2.5-flash-lite"), 20_000)
    assert plan(registry, objective=Objective.SPEED).primary.spec.id != "gemini-2.5-flash-lite"


def test_capability_requirements_filter_models(registry: ProviderRegistry) -> None:
    result = plan(registry, RoutingRequirements(50, capabilities=frozenset({Capability.REASONING})))
    assert all(Capability.REASONING in c.spec.capabilities for c in result.candidates)
    openai = next(c for c in result.candidates if c.spec.provider is ProviderName.OPENAI)
    assert openai.spec.id == "o4-mini"


def test_context_window_and_output_limits(registry: ProviderRegistry) -> None:
    long_prompt = plan(registry, RoutingRequirements(prompt_tokens=300_000))
    assert all(c.spec.context_window >= 1_000_000 for c in long_prompt.candidates)
    assert ProviderName.ANTHROPIC not in {c.spec.provider for c in long_prompt.candidates}

    big_output = plan(
        registry, RoutingRequirements(50, max_tokens=50_000), objective=Objective.QUALITY
    )
    assert all(c.spec.max_output_tokens >= 50_000 for c in big_output.candidates)
    assert all(c.max_tokens == 50_000 for c in big_output.candidates)


def test_default_output_budget_respects_model_limit(synthetic: ProviderRegistry) -> None:
    result = RoutingPolicy(synthetic, default_max_tokens=100_000).plan(
        DEFAULT, mode=RoutingMode.AUTO, objective=Objective.COST, policy=OrganizationPolicy()
    )
    assert all(c.max_tokens == 8_192 for c in result.candidates)


# --- PREFERRED -------------------------------------------------------------------


def test_preferred_model_is_honoured_with_fallbacks(registry: ProviderRegistry) -> None:
    result = plan(registry, mode=RoutingMode.PREFERRED, model="claude-haiku-4-5")
    assert result.primary.spec.key == "anthropic/claude-haiku-4-5"
    assert result.reason == "preferred: anthropic/claude-haiku-4-5 as requested"
    assert [c.spec.provider for c in result.candidates] == [
        ProviderName.ANTHROPIC,
        ProviderName.OPENAI,
        ProviderName.GEMINI,
    ]


def test_preferred_provider_picks_its_best_model(registry: ProviderRegistry) -> None:
    result = plan(
        registry,
        mode=RoutingMode.PREFERRED,
        provider=ProviderName.GEMINI,
        objective=Objective.QUALITY,
    )
    assert result.primary.spec.key == "gemini/gemini-2.5-pro"


def test_preferred_reroutes_when_unavailable(
    registry: ProviderRegistry, providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[ProviderName.ANTHROPIC].is_configured = False
    result = plan(registry, mode=RoutingMode.PREFERRED, model="claude-sonnet-4-5")
    assert result.primary.spec.provider is not ProviderName.ANTHROPIC
    assert "anthropic/claude-sonnet-4-5 unavailable (provider not_configured)" in result.reason
    assert ProviderName.ANTHROPIC not in {c.spec.provider for c in result.candidates}


# --- STRICT ------------------------------------------------------------------------


def test_strict_uses_only_the_requested_model(registry: ProviderRegistry) -> None:
    result = plan(registry, mode=RoutingMode.STRICT, model="gpt-4.1")
    assert keys(result) == ["openai/gpt-4.1"]
    assert result.reason == "strict: openai/gpt-4.1 as requested"


def test_strict_fails_when_model_unavailable(registry: ProviderRegistry) -> None:
    gpt = registry.resolve("gpt-4.1")
    for _ in range(3):
        registry.record_failure(
            gpt, ProviderError(ProviderName.OPENAI, ProviderErrorKind.SERVER, "x")
        )
    with pytest.raises(NoRouteError, match="circuit_open") as info:
        plan(registry, mode=RoutingMode.STRICT, model="gpt-4.1")
    assert info.value.status_code == 503
    assert info.value.rejections == {"openai/gpt-4.1": "provider circuit_open"}

    with pytest.raises(NoRouteError, match="exceeds model limit"):
        plan(
            registry,
            RoutingRequirements(50, max_tokens=64_000),
            mode=RoutingMode.STRICT,
            model="claude-opus-4-1",
        )


def test_modes_require_targets(registry: ProviderRegistry) -> None:
    with pytest.raises(RoutingError, match="requires a model"):
        plan(registry, mode=RoutingMode.STRICT, provider=ProviderName.OPENAI)
    with pytest.raises(RoutingError, match="requires a model or provider"):
        plan(registry, mode=RoutingMode.PREFERRED)


# --- Organization policy ---------------------------------------------------------


def test_policy_restricts_providers_and_models(registry: ProviderRegistry) -> None:
    anthropic_only = plan(
        registry, policy=OrganizationPolicy(allowed_providers=[ProviderName.ANTHROPIC])
    )
    assert {c.spec.provider for c in anthropic_only.candidates} == {ProviderName.ANTHROPIC}
    assert len(anthropic_only.candidates) == 1, "no other provider to fall back to"

    blocked = plan(
        registry,
        objective=Objective.QUALITY,
        policy=OrganizationPolicy(blocked_models=["claude-sonnet-4-5", "gemini/gemini-2.5-pro"]),
    )
    assert not {"anthropic/claude-sonnet-4-5", "gemini/gemini-2.5-pro"} & set(keys(blocked))


@pytest.mark.parametrize("mode", [RoutingMode.PREFERRED, RoutingMode.STRICT])
def test_explicit_request_for_disallowed_model_is_rejected(
    registry: ProviderRegistry, mode: RoutingMode
) -> None:
    policy = OrganizationPolicy(blocked_models=["openai/gpt-4.1"])
    with pytest.raises(PolicyViolationError) as info:
        plan(registry, mode=mode, model="gpt-4.1", policy=policy)
    assert info.value.status_code == 403

    with pytest.raises(PolicyViolationError):
        plan(
            registry,
            mode=RoutingMode.PREFERRED,
            provider=ProviderName.GEMINI,
            policy=OrganizationPolicy(allowed_providers=[ProviderName.OPENAI]),
        )


def test_policy_cost_ceiling(registry: ProviderRegistry) -> None:
    result = plan(registry, policy=OrganizationPolicy(max_cost_per_request_usd=Decimal("0.001")))
    assert keys(result) == ["gemini/gemini-2.5-flash-lite"]
    assert all(c.max_cost <= Decimal("0.001") for c in result.candidates)


def test_fallback_can_be_disabled(registry: ProviderRegistry) -> None:
    assert len(plan(registry, policy=OrganizationPolicy(allow_fallback=False)).candidates) == 1
    assert len(plan(registry, allow_fallback=False).candidates) == 1


def test_no_route_lists_every_rejection(
    registry: ProviderRegistry, providers: dict[ProviderName, FakeProvider]
) -> None:
    for provider in providers.values():
        provider.is_configured = False
    with pytest.raises(NoRouteError) as info:
        plan(registry)
    assert set(info.value.rejections) == {s.key for s in registry.models()}


def test_policy_parsing_fails_closed() -> None:
    assert OrganizationPolicy.from_organization_settings({}) == OrganizationPolicy()
    parsed = OrganizationPolicy.from_organization_settings(
        {"routing": {"allowed_providers": ["gemini"], "default_objective": "cost"}}
    )
    assert parsed.allowed_providers == [ProviderName.GEMINI]
    assert parsed.default_objective is Objective.COST
    with pytest.raises(InvalidPolicyError):
        OrganizationPolicy.from_organization_settings({"routing": {"allowed_providers": ["aol"]}})
    with pytest.raises(InvalidPolicyError):
        OrganizationPolicy.from_organization_settings({"routing": {"surprise": True}})


# --- Router-level mode resolution --------------------------------------------------

USER = (ChatMessage(MessageRole.USER, "hello"),)


def test_router_defaults_mode_from_request(cortex_router: CortexRouter) -> None:
    assert cortex_router.plan(CompletionTask(USER)).mode is RoutingMode.AUTO
    assert cortex_router.plan(CompletionTask(USER, model="gpt-4.1")).mode is RoutingMode.PREFERRED
    assert (
        cortex_router.plan(CompletionTask(USER, provider=ProviderName.GEMINI)).primary.spec.provider
        is ProviderName.GEMINI
    )
    org_default = CompletionTask(USER, policy=OrganizationPolicy(default_objective=Objective.COST))
    assert cortex_router.plan(org_default).objective is Objective.COST
    with pytest.raises(UnknownModelError):
        cortex_router.plan(CompletionTask(USER, model="gpt-17"))
