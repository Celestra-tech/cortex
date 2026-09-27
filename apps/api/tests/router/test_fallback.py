import pytest

from cortex_api.models.message import MessageRole
from cortex_api.models.model_execution import RoutingMode
from cortex_api.services.router.base import (
    ChatMessage,
    FinishReason,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    ProviderRequest,
    ProviderResponse,
)
from cortex_api.services.router.registry import ProviderRegistry
from cortex_api.services.router.router import CompletionFailedError, CompletionTask, CortexRouter

from .fakes import FakeProvider, SleepRecorder

OPENAI, ANTHROPIC, GEMINI = ProviderName.OPENAI, ProviderName.ANTHROPIC, ProviderName.GEMINI
USER = (ChatMessage(MessageRole.USER, "Summarize the quarterly report."),)
PREFER_OPENAI = CompletionTask(USER, model="gpt-4.1")


async def test_primary_success_is_a_single_attempt(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider], sleeps: SleepRecorder
) -> None:
    routed = await cortex_router.complete(PREFER_OPENAI)
    assert routed.spec.key == "openai/gpt-4.1"
    assert [a.success for a in routed.attempts] == [True]
    assert routed.response.output == "openai answered with gpt-4.1"
    assert (routed.prompt_tokens, routed.completion_tokens) == (120, 30)
    assert routed.cost_estimate == routed.spec.estimate_cost(120, 30)
    assert len(providers[ANTHROPIC].calls) == 0
    assert sleeps.delays == []


async def test_retry_then_fall_back_to_next_provider(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider], sleeps: SleepRecorder
) -> None:
    providers[OPENAI].fail(ProviderErrorKind.SERVER, times=2)

    routed = await cortex_router.complete(PREFER_OPENAI)

    assert routed.spec.provider is ANTHROPIC
    assert [(a.spec.provider, a.success, a.is_fallback) for a in routed.attempts] == [
        (OPENAI, False, False),
        (OPENAI, False, False),
        (ANTHROPIC, True, True),
    ]
    first = routed.attempts[0].error
    assert first is not None
    assert first.describe() == "server_error (HTTP 500): upstream exploded"
    assert [a.number for a in routed.attempts] == [1, 2, 3]
    assert len(sleeps.delays) == 1
    assert 0.125 <= sleeps.delays[0] <= 0.25
    assert routed.plan.reason == "preferred: openai/gpt-4.1 as requested"


async def test_full_chain_openai_anthropic_gemini(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider], sleeps: SleepRecorder
) -> None:
    providers[OPENAI].fail(ProviderErrorKind.AUTHENTICATION)
    providers[ANTHROPIC].fail(ProviderErrorKind.RATE_LIMITED, times=2, retry_after_seconds=1.5)

    routed = await cortex_router.complete(PREFER_OPENAI)

    assert routed.spec.provider is GEMINI
    assert [a.spec.provider for a in routed.attempts] == [OPENAI, ANTHROPIC, ANTHROPIC, GEMINI]
    assert len(providers[OPENAI].calls) == 1, "authentication errors are not retried"
    assert sleeps.delays == [1.5], "Retry-After is honoured"


async def test_all_providers_failing_raises_with_every_attempt(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider]
) -> None:
    for provider in providers.values():
        provider.fail(ProviderErrorKind.BAD_REQUEST, message="prompt rejected")

    with pytest.raises(CompletionFailedError) as info:
        await cortex_router.complete(PREFER_OPENAI)

    error = info.value
    assert error.status_code == 502
    assert [a.spec.provider for a in error.attempts] == [OPENAI, ANTHROPIC, GEMINI]
    assert all(not a.success for a in error.attempts)
    assert error.plan.mode is RoutingMode.PREFERRED
    assert "openai/gpt-4.1: bad_request (HTTP 400): prompt rejected" in str(error)


async def test_strict_mode_never_falls_back(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[OPENAI].fail(ProviderErrorKind.SERVER, times=2)
    with pytest.raises(CompletionFailedError) as info:
        await cortex_router.complete(CompletionTask(USER, model="gpt-4.1", mode=RoutingMode.STRICT))
    assert [a.spec.key for a in info.value.attempts] == ["openai/gpt-4.1"] * 2
    assert providers[ANTHROPIC].calls == []


async def test_circuit_opening_mid_request_stops_retries(
    cortex_router: CortexRouter,
    registry: ProviderRegistry,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    gpt = registry.resolve("gpt-4.1")
    for _ in range(2):
        registry.record_failure(gpt, ProviderError(OPENAI, ProviderErrorKind.TIMEOUT, "slow"))
    providers[OPENAI].fail(ProviderErrorKind.TIMEOUT)

    routed = await cortex_router.complete(PREFER_OPENAI)

    assert len(providers[OPENAI].calls) == 1, "third failure opened the circuit; no retry"
    assert routed.spec.provider is ANTHROPIC
    assert registry.health(OPENAI).circuit_open


async def test_fallback_candidates_skipped_when_circuit_opens(
    cortex_router: CortexRouter,
    registry: ProviderRegistry,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    plan = cortex_router.plan(PREFER_OPENAI)
    sonnet = registry.resolve("claude-sonnet-4-5")
    for _ in range(3):
        registry.record_failure(sonnet, ProviderError(ANTHROPIC, ProviderErrorKind.SERVER, "down"))
    providers[OPENAI].fail(ProviderErrorKind.SERVER, times=2)

    result = await cortex_router.executor.execute(
        plan, lambda c: ProviderRequest(c.spec, USER, c.max_tokens)
    )
    assert result.candidate.spec.provider is GEMINI
    assert providers[ANTHROPIC].calls == []


async def test_unexpected_adapter_exception_is_contained(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[OPENAI].script.append(RuntimeError("adapter bug"))
    routed = await cortex_router.complete(PREFER_OPENAI)
    failure = routed.attempts[0].error
    assert failure is not None
    assert failure.kind is ProviderErrorKind.INVALID_RESPONSE
    assert "RuntimeError: adapter bug" in failure.message
    assert routed.spec.provider is ANTHROPIC


async def test_health_stats_follow_outcomes(
    cortex_router: CortexRouter,
    registry: ProviderRegistry,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    providers[OPENAI].fail(ProviderErrorKind.SERVER, times=2)
    await cortex_router.complete(PREFER_OPENAI)
    assert registry.health(OPENAI).consecutive_failures == 2
    assert registry.health(ANTHROPIC).consecutive_failures == 0
    sonnet = registry.resolve("claude-sonnet-4-5")
    assert registry.expected_latency_ms(sonnet) < sonnet.typical_latency_ms


async def test_missing_usage_is_estimated(
    cortex_router: CortexRouter, providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[OPENAI].script.append(
        ProviderResponse(
            output="x" * 400,
            prompt_tokens=0,
            completion_tokens=0,
            finish_reason=FinishReason.STOP,
            provider_model="gpt-4.1",
        )
    )
    routed = await cortex_router.complete(PREFER_OPENAI)
    assert routed.completion_tokens == 100
    assert routed.prompt_tokens == cortex_router.estimate_prompt_tokens(USER)
    assert routed.prompt_tokens > 0
