import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from cortex_api import SERVICE_NAME, __version__
from cortex_api.api import v1
from cortex_api.api.errors import register_exception_handlers
from cortex_api.api.routes import health
from cortex_api.cache.redis import create_redis
from cortex_api.core.config import Settings, get_settings
from cortex_api.core.http import (
    BodySizeLimitMiddleware,
    ObservabilityMiddleware,
    SecurityHeadersMiddleware,
)
from cortex_api.core.logging import configure_logging
from cortex_api.core.metrics import MetricsServer, set_build_info
from cortex_api.core.rate_limit import RateLimitHeadersMiddleware
from cortex_api.core.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from cortex_api.core.tracing import (
    configure_tracing,
    instrument_app,
    instrument_engine,
    shutdown_tracing,
)
from cortex_api.database.config import DatabaseConfig
from cortex_api.database.session import create_engine, create_sessionmaker
from cortex_api.services.knowledge.embeddings import build_embedder
from cortex_api.services.router.router import CortexRouter

logger = logging.getLogger(__name__)

CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
CORS_HEADERS = [
    "Authorization",
    "Content-Type",
    "Accept",
    "Last-Event-ID",
    "X-Organization-ID",
    REQUEST_ID_HEADER,
    "X-Cortex-Client",
]
CORS_EXPOSED = [
    REQUEST_ID_HEADER,
    "Retry-After",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    engine = create_engine(DatabaseConfig.from_settings(settings))
    instrument_engine(engine, app.state.tracer_provider)
    app.state.db_engine = engine
    app.state.db_sessionmaker = create_sessionmaker(engine)
    app.state.redis = create_redis(settings)
    http_client = httpx2.AsyncClient(
        timeout=httpx2.Timeout(settings.router_timeout_seconds, connect=10.0),
        limits=httpx2.Limits(max_connections=200, max_keepalive_connections=50),
    )
    app.state.http_client = http_client
    app.state.router = CortexRouter.from_settings(settings, http_client)
    app.state.embedder = build_embedder(settings, http_client)

    metrics_server = None
    if settings.metrics_enabled:
        set_build_info(
            version=__version__,
            release=settings.release,
            revision=settings.revision,
            env=settings.env,
        )
        if settings.metrics_port is not None:
            metrics_server = MetricsServer(settings.metrics_port)
            metrics_server.start()

    configured = [p.name for p in app.state.router.registry.providers() if p.configured]
    logger.info(
        "%s %s started (env=%s, release=%s, providers=%s, embeddings=%s)",
        SERVICE_NAME,
        __version__,
        settings.env,
        settings.release or "-",
        ",".join(configured) or "none",
        app.state.embedder.space,
    )
    if not configured:
        logger.warning("No chat provider has an API key; completions will fail")
    if not app.state.embedder.provider.configured:
        logger.warning("Embedding provider %s has no API key", app.state.embedder.space)
    elif settings.knowledge_embedding_provider == "local" and settings.is_production:
        logger.warning("Local hashing embeddings are lexical; configure openai or gemini")
    try:
        yield
    finally:
        if metrics_server is not None:
            metrics_server.stop()
        await http_client.aclose()
        await app.state.redis.aclose()
        await engine.dispose()
        shutdown_tracing()
        logger.info("%s stopped", SERVICE_NAME)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(
        settings.log_level,
        settings.log_format,
        static_fields={
            "service": SERVICE_NAME,
            "env": settings.env,
            "release": settings.release or __version__,
        },
        gcp_project_id=settings.gcp_project_id,
    )

    app = FastAPI(
        title=settings.api_title,
        version=__version__,
        lifespan=lifespan,
        docs_url=None if settings.is_deployed else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_deployed else "/openapi.json",
    )
    app.state.settings = settings
    app.state.tracer_provider = configure_tracing(settings)

    # Added innermost first; the request passes through them in reverse order.
    app.add_middleware(RateLimitHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        # API keys travel in the Authorization header, never cookies.
        allow_credentials=False,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        expose_headers=CORS_EXPOSED,
        max_age=600,
    )
    if settings.api_allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.api_allowed_hosts)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.api_max_request_bytes)
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_seconds=settings.security_hsts_seconds if settings.is_deployed else None,
    )
    app.add_middleware(ObservabilityMiddleware)
    # Outermost, so every response (preflights, 413s, errors) and every log record has the ID.
    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(v1.router)
    instrument_app(app, app.state.tracer_provider)
    return app


app = create_app()
