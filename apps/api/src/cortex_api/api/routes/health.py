import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from cortex_api import SERVICE_NAME, __version__
from cortex_api.api.deps import EmbedderDep, RedisDep, RouterDep, SettingsDep
from cortex_api.core.health import check_postgres, check_redis
from cortex_api.database.session import get_engine
from cortex_api.schemas.health import (
    DatabaseHealthResponse,
    DependencyChecks,
    EmbeddingHealth,
    HealthResponse,
    ProviderHealth,
    ProvidersHealthResponse,
    ReadinessResponse,
    RedisHealthResponse,
)

router = APIRouter(prefix="/health", tags=["health"])

EngineDep = Annotated[AsyncEngine, Depends(get_engine)]


@router.get("", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    return HealthResponse(status="healthy", service=SERVICE_NAME, version=__version__)


@router.get(
    "/database",
    response_model=DatabaseHealthResponse,
    summary="PostgreSQL connectivity",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": DatabaseHealthResponse}},
)
async def database_health(
    response: Response, settings: SettingsDep, engine: EngineDep
) -> DatabaseHealthResponse:
    check = await check_postgres(engine, settings.health_check_timeout_seconds)
    if check.status == "down":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return DatabaseHealthResponse(status="unhealthy", database="disconnected")
    return DatabaseHealthResponse(status="healthy", database="connected")


@router.get(
    "/redis",
    response_model=RedisHealthResponse,
    summary="Redis connectivity",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": RedisHealthResponse}},
)
async def redis_health(
    response: Response, settings: SettingsDep, redis: RedisDep
) -> RedisHealthResponse:
    check = await check_redis(redis, settings.health_check_timeout_seconds)
    if check.status == "down":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return RedisHealthResponse(status="unhealthy", redis="disconnected")
    return RedisHealthResponse(status="healthy", redis="connected", latency_ms=check.latency_ms)


@router.get(
    "/providers",
    response_model=ProvidersHealthResponse,
    summary="Model provider availability",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ProvidersHealthResponse}},
)
async def providers_health(
    response: Response, cortex_router: RouterDep, embedder: EmbedderDep
) -> ProvidersHealthResponse:
    """Credentials and circuit-breaker state of this instance; makes no provider calls.

    Probing providers would spend money and rate limit on every scrape, while
    real traffic already trips the breakers within a few failures.
    """
    registry = cortex_router.registry
    providers = []
    for provider in registry.providers():
        snapshot = registry.health(provider.name)
        providers.append(
            ProviderHealth(
                name=snapshot.provider,
                status=(
                    "unconfigured"
                    if not snapshot.configured
                    else "circuit_open"
                    if snapshot.circuit_open
                    else "available"
                ),
                configured=snapshot.configured,
                circuit_open=snapshot.circuit_open,
                consecutive_failures=snapshot.consecutive_failures,
                last_error=snapshot.last_error,
            )
        )
    available = sum(p.status == "available" for p in providers)
    configured = sum(p.configured for p in providers)
    if available == 0:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ProvidersHealthResponse(
        status="unhealthy"
        if available == 0
        else "degraded"
        if available < configured
        else "healthy",
        available=available,
        providers=providers,
        embeddings=EmbeddingHealth(space=embedder.space, configured=embedder.provider.configured),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    response: Response,
    settings: SettingsDep,
    engine: EngineDep,
    redis: RedisDep,
) -> ReadinessResponse:
    timeout = settings.health_check_timeout_seconds
    postgres_check, redis_check = await asyncio.gather(
        check_postgres(engine, timeout),
        check_redis(redis, timeout),
    )
    checks = DependencyChecks(postgres=postgres_check, redis=redis_check)
    healthy = postgres_check.status == "up" and redis_check.status == "up"
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="healthy" if healthy else "degraded",
        service=SERVICE_NAME,
        version=__version__,
        checks=checks,
    )
