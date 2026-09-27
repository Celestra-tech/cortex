from decimal import Decimal

import httpx2
import pytest
from pydantic import SecretStr

from cortex_api.core.config import Settings
from cortex_api.services.router.base import (
    ModelSpec,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
)
from cortex_api.services.router.registry import (
    AmbiguousModelError,
    AvailabilityReason,
    ProviderRegistry,
    UnknownModelError,
    UnknownProviderError,
)
from cortex_api.services.router.router import build_registry

from .fakes import FakeClock, FakeProvider


def spec(
    model_id: str, provider: ProviderName = ProviderName.LLAMA, **overrides: object
) -> ModelSpec:
    values: dict[str, object] = {
        "id": model_id,
        "provider": provider,
        "display_name": model_id,
        "context_window": 8_192,
        "max_output_tokens": 2_048,
        "input_cost_per_mtok": Decimal("0.10"),
        "output_cost_per_mtok": Decimal("0.20"),
        "quality": 2,
        "typical_latency_ms": 500,
        **overrides,
    }
    return ModelSpec(**values)  # type: ignore[arg-type]


def error(kind: ProviderErrorKind, provider: ProviderName = ProviderName.OPENAI) -> ProviderError:
    return ProviderError(provider, kind, "boom")


def test_catalog_lists_all_registered_models(registry: ProviderRegistry) -> None:
    keys = [s.key for s in registry.models()]
    assert "openai/gpt-4.1" in keys
    assert "anthropic/claude-sonnet-4-5" in keys
    assert "gemini/gemini-2.5-flash" in keys
    assert {p.name for p in registry.providers()} == {
        ProviderName.OPENAI,
        ProviderName.ANTHROPIC,
        ProviderName.GEMINI,
    }
    assert all(s.provider is ProviderName.GEMINI for s in registry.models(ProviderName.GEMINI))


def test_resolve_by_key_id_and_alias(registry: ProviderRegistry) -> None:
    assert registry.resolve("openai/gpt-4.1").id == "gpt-4.1"
    assert registry.resolve("GPT-4.1").key == "openai/gpt-4.1"
    assert registry.resolve("gemini-2.5-pro", provider=ProviderName.GEMINI).provider is (
        ProviderName.GEMINI
    )

    custom = ProviderRegistry()
    custom.register(FakeProvider(ProviderName.LLAMA, [spec("llama-3.3-70b", aliases=("llama3",))]))
    assert custom.resolve("llama3").id == "llama-3.3-70b"


@pytest.mark.parametrize(
    "reference", ["gpt-9", "unknown/gpt-4.1", "anthropic/gpt-4.1", "gemini/claude-haiku-4-5"]
)
def test_resolve_unknown_models(registry: ProviderRegistry, reference: str) -> None:
    with pytest.raises(UnknownModelError):
        registry.resolve(reference)


def test_resolve_requires_provider_when_ambiguous() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider(ProviderName.LLAMA, [spec("llama-3.3-70b")]))
    registry.register(
        FakeProvider(ProviderName.QWEN, [spec("llama-3.3-70b", provider=ProviderName.QWEN)])
    )
    with pytest.raises(AmbiguousModelError) as info:
        registry.resolve("llama-3.3-70b")
    assert "llama/llama-3.3-70b" in str(info.value)
    assert registry.resolve("qwen/llama-3.3-70b").provider is ProviderName.QWEN


def test_registration_guards() -> None:
    registry = ProviderRegistry()
    registry.register(FakeProvider(ProviderName.LLAMA, [spec("a")]))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeProvider(ProviderName.LLAMA, [spec("b")]))
    with pytest.raises(ValueError, match="Duplicate model keys"):
        registry.register(
            FakeProvider(
                ProviderName.QWEN,
                [spec("x", provider=ProviderName.QWEN), spec("x", provider=ProviderName.QWEN)],
            )
        )
    with pytest.raises(ValueError, match="foreign models"):
        FakeProvider(ProviderName.DEEPSEEK, [spec("y", provider=ProviderName.QWEN)])
    with pytest.raises(UnknownProviderError):
        registry.provider(ProviderName.GEMINI)
    with pytest.raises(UnknownProviderError):
        registry.provider("not-a-provider")


def test_unconfigured_provider_is_unavailable(
    registry: ProviderRegistry, providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[ProviderName.ANTHROPIC].is_configured = False
    sonnet = registry.resolve("claude-sonnet-4-5")
    assert registry.availability(sonnet).reason is AvailabilityReason.NOT_CONFIGURED
    assert registry.is_available(registry.resolve("gpt-4.1"))


def test_circuit_breaker_opens_and_half_opens(registry: ProviderRegistry, clock: FakeClock) -> None:
    gpt = registry.resolve("gpt-4.1")
    mini = registry.resolve("gpt-4.1-mini")

    registry.record_failure(gpt, error(ProviderErrorKind.BAD_REQUEST))
    assert registry.health(ProviderName.OPENAI).consecutive_failures == 0, "caller errors ignored"

    for _ in range(3):
        registry.record_failure(gpt, error(ProviderErrorKind.SERVER))
    assert registry.availability(gpt).reason is AvailabilityReason.CIRCUIT_OPEN
    assert not registry.is_available(mini), "circuit is provider-wide"
    assert registry.is_available(registry.resolve("claude-haiku-4-5"))
    snapshot = registry.health(ProviderName.OPENAI)
    assert snapshot.circuit_open
    assert snapshot.last_error == "server_error: boom"

    clock.advance(30.1)
    assert registry.is_available(gpt), "cooldown elapsed: probe allowed"
    registry.record_failure(gpt, error(ProviderErrorKind.TIMEOUT))
    assert not registry.is_available(gpt), "failed probe re-opens immediately"

    clock.advance(30.1)
    registry.record_success(gpt, 900)
    assert registry.is_available(gpt)
    assert registry.health(ProviderName.OPENAI).consecutive_failures == 0


def test_latency_is_an_ewma_over_the_catalog_prior(registry: ProviderRegistry) -> None:
    haiku = registry.resolve("claude-haiku-4-5")
    assert registry.expected_latency_ms(haiku) == haiku.typical_latency_ms
    registry.record_success(haiku, 1_000)
    assert registry.expected_latency_ms(haiku) == 1_000
    registry.record_success(haiku, 2_000)
    assert registry.expected_latency_ms(haiku) == pytest.approx(1_200)


def test_cost_estimate_uses_per_million_pricing(registry: ProviderRegistry) -> None:
    sonnet = registry.resolve("claude-sonnet-4-5")
    assert sonnet.estimate_cost(1_000_000, 0) == Decimal("3.00000000")
    assert sonnet.estimate_cost(1_000, 500) == Decimal("0.01050000")


async def test_build_registry_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CORTEX_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(
        env="test",
        openai_api_key=SecretStr("sk-test"),
        gemini_api_key=SecretStr("g-test"),
        router_circuit_failure_threshold=5,
        _env_file=None,
    )
    async with httpx2.AsyncClient() as client:
        registry = build_registry(settings, client)

    configured = {p.name: p.configured for p in registry.providers()}
    assert configured == {
        ProviderName.OPENAI: True,
        ProviderName.ANTHROPIC: False,
        ProviderName.GEMINI: True,
    }
    assert registry.failure_threshold == 5
    assert len(registry.models()) == 9


def test_settings_accept_vendor_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("CORTEX_ROUTER_FALLBACK_ORDER", "gemini, openai")
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert settings.router_fallback_order == ["gemini", "openai"]


def test_cortex_key_wins_and_blank_values_fall_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-vendor")
    monkeypatch.setenv("CORTEX_OPENAI_API_KEY", "sk-cortex")
    monkeypatch.setenv("GEMINI_API_KEY", "g-vendor")
    monkeypatch.setenv("CORTEX_GEMINI_API_KEY", "")
    settings = Settings(_env_file=None)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == "sk-cortex"
    assert settings.gemini_api_key is not None
    assert settings.gemini_api_key.get_secret_value() == "g-vendor"
