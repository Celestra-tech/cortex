import httpx2
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from cortex_api.services.router import telemetry
from cortex_api.services.router.base import ProviderError, ProviderErrorKind, ProviderName
from cortex_api.services.router.router import CompletionTask, CortexRouter

from .fakes import CATALOGS, FakeProvider
from .test_fallback import PREFER_OPENAI


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestProviderTelemetry:
    @pytest.fixture
    def spans(self, monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
        return exporter

    async def test_success_records_calls_tokens_cost_and_a_span(
        self, cortex_router: CortexRouter, spans: InMemorySpanExporter
    ) -> None:
        labels = {"provider": "openai", "model": "gpt-4.1"}
        before = (
            sample("cortex_provider_requests_total", **labels, outcome="success"),
            sample("cortex_provider_tokens_total", **labels, direction="input"),
            sample("cortex_provider_cost_usd_total", **labels),
        )
        await cortex_router.complete(PREFER_OPENAI)

        assert (
            sample("cortex_provider_requests_total", **labels, outcome="success") == before[0] + 1
        )
        assert (
            sample("cortex_provider_tokens_total", **labels, direction="input") == before[1] + 120
        )
        assert sample("cortex_provider_cost_usd_total", **labels) > before[2]
        assert sample("cortex_provider_circuit_open", provider="openai") == 0

        (span,) = spans.get_finished_spans()
        assert span.name == "chat gpt-4.1"
        assert span.attributes is not None
        assert span.attributes["gen_ai.system"] == "openai"
        assert span.attributes["gen_ai.usage.output_tokens"] == 30
        assert span.attributes["cortex.fallback"] is False

    async def test_failures_are_labelled_by_kind_and_open_circuits_show(
        self,
        cortex_router: CortexRouter,
        providers: dict[ProviderName, FakeProvider],
        spans: InMemorySpanExporter,
    ) -> None:
        labels = {"provider": "openai", "model": "gpt-4.1"}
        before = sample("cortex_provider_requests_total", **labels, outcome="server_error")
        providers[ProviderName.OPENAI].fail(ProviderErrorKind.SERVER, times=3)
        for _ in range(2):
            await cortex_router.complete(CompletionTask(PREFER_OPENAI.messages, model="gpt-4.1"))

        assert sample("cortex_provider_requests_total", **labels, outcome="server_error") == (
            before + 3
        )
        assert sample("cortex_provider_circuit_open", provider="openai") == 1
        failed = [
            s for s in spans.get_finished_spans() if s.attributes and "error.type" in s.attributes
        ]
        assert failed and failed[0].status.status_code.name == "ERROR"


@pytest.mark.database
@pytest.mark.redis
class TestProvidersHealth:
    @pytest.fixture
    def providers(self) -> dict[ProviderName, FakeProvider]:
        providers = {name: FakeProvider(name, models) for name, models in CATALOGS.items()}
        providers[ProviderName.GEMINI].is_configured = False
        return providers

    async def test_reports_each_provider(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/health/providers")
        assert response.status_code == 200
        body = response.json()
        statuses = {p["name"]: p["status"] for p in body["providers"]}
        assert statuses == {
            "openai": "available",
            "anthropic": "available",
            "gemini": "unconfigured",
        }
        assert body["status"] == "healthy" and body["available"] == 2
        assert body["embeddings"]["configured"] is True

    async def test_open_circuits_degrade_and_none_left_is_unhealthy(
        self, api: httpx2.AsyncClient, cortex_router: CortexRouter
    ) -> None:
        registry = cortex_router.registry
        for name in (ProviderName.OPENAI, ProviderName.ANTHROPIC):
            spec = registry.models(name)[0]
            for _ in range(3):
                registry.record_failure(spec, ProviderError(name, ProviderErrorKind.SERVER, "down"))
            if name is ProviderName.OPENAI:
                degraded = await api.get("/health/providers")
                assert degraded.status_code == 200
                assert degraded.json()["status"] == "degraded"

        down = await api.get("/health/providers")
        assert down.status_code == 503
        assert down.json()["status"] == "unhealthy"
