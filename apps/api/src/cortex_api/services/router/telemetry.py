"""Metrics and spans for provider calls, recorded per attempt by the fallback executor.

Span attributes follow the OpenTelemetry GenAI semantic conventions. Prompts
and completions are never attached: they are tenant data.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from cortex_api.core.metrics import (
    PROVIDER_CIRCUIT_OPEN,
    PROVIDER_COST,
    PROVIDER_LATENCY,
    PROVIDER_REQUESTS,
    PROVIDER_TOKENS,
)
from cortex_api.services.router.base import (
    ModelSpec,
    ProviderError,
    ProviderName,
    ProviderResponse,
)
from cortex_api.services.router.registry import ProviderRegistry

tracer = trace.get_tracer("cortex_api.router")


@contextmanager
def provider_span(spec: ModelSpec, *, attempt: int, fallback: bool) -> Iterator[Span]:
    with tracer.start_as_current_span(
        f"chat {spec.id}",
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.system": spec.provider,
            "gen_ai.request.model": spec.id,
            "cortex.attempt": attempt,
            "cortex.fallback": fallback,
        },
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        yield span


def record_success(
    span: Span, spec: ModelSpec, response: ProviderResponse, latency_ms: int
) -> None:
    labels = (spec.provider, spec.id)
    PROVIDER_REQUESTS.labels(*labels, "success").inc()
    PROVIDER_LATENCY.labels(*labels).observe(latency_ms / 1000)
    PROVIDER_TOKENS.labels(*labels, "input").inc(response.prompt_tokens)
    PROVIDER_TOKENS.labels(*labels, "output").inc(response.completion_tokens)
    PROVIDER_COST.labels(*labels).inc(
        float(spec.estimate_cost(response.prompt_tokens, response.completion_tokens))
    )
    if span.is_recording():
        span.set_attributes(
            {
                "gen_ai.response.model": response.provider_model,
                "gen_ai.response.finish_reasons": [str(response.finish_reason)],
                "gen_ai.usage.input_tokens": response.prompt_tokens,
                "gen_ai.usage.output_tokens": response.completion_tokens,
            }
        )
        if response.provider_request_id:
            span.set_attribute("gen_ai.response.id", response.provider_request_id)


def record_failure(span: Span, spec: ModelSpec, error: ProviderError, latency_ms: int) -> None:
    PROVIDER_REQUESTS.labels(spec.provider, spec.id, str(error.kind)).inc()
    PROVIDER_LATENCY.labels(spec.provider, spec.id).observe(latency_ms / 1000)
    if span.is_recording():
        span.set_attribute("error.type", str(error.kind))
        if error.status_code is not None:
            span.set_attribute("http.response.status_code", error.status_code)
        span.set_status(Status(StatusCode.ERROR, error.describe()))


def record_skip(spec: ModelSpec) -> None:
    PROVIDER_REQUESTS.labels(spec.provider, spec.id, "skipped").inc()


def record_circuit(registry: ProviderRegistry, provider: ProviderName) -> None:
    PROVIDER_CIRCUIT_OPEN.labels(provider).set(1 if registry.health(provider).circuit_open else 0)
