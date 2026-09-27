"""OpenTelemetry tracing: FastAPI, SQLAlchemy and Redis spans, exported over OTLP/HTTP.

Provider calls go through httpx2, which has no auto-instrumentation, so the
router opens those spans itself (`cortex_api.services.router.fallback`).
With tracing disabled the global no-op tracer makes all of it free.
"""

import logging
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from sqlalchemy.ext.asyncio import AsyncEngine

from cortex_api import __version__
from cortex_api.core.config import Settings

logger = logging.getLogger(__name__)

# Regexes searched in the full URL. Probes would otherwise dominate trace volume.
EXCLUDED_URLS = "/health"

_provider: TracerProvider | None = None


def configure_tracing(settings: Settings) -> TracerProvider | None:
    """Install the global tracer provider once per process; None when disabled."""
    global _provider
    if not settings.otel_enabled:
        return None
    if _provider is not None:
        return _provider

    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.redis import RedisInstrumentor

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.release or __version__,
            "deployment.environment.name": settings.env,
            **({"vcs.ref.head.revision": settings.revision} if settings.revision else {}),
        }
    )
    provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(settings.otel_sample_ratio))
    )
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(f"{endpoint}/v1/traces")))
    trace.set_tracer_provider(provider)
    RedisInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    logger.info("Tracing to %s (sample ratio %s)", endpoint, settings.otel_sample_ratio)
    return provider


def instrument_app(app: FastAPI, provider: TracerProvider | None) -> None:
    if provider is None:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS)


def instrument_engine(engine: AsyncEngine, provider: TracerProvider | None) -> Any:
    if provider is None:
        return None
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    return SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, tracer_provider=provider, enable_commenter=False
    )


def shutdown_tracing() -> None:
    """Flush buffered spans; Cloud Run gives a few seconds after SIGTERM."""
    if _provider is not None:
        _provider.force_flush(timeout_millis=5000)
