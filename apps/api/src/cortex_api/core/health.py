import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from cortex_api.schemas.health import DependencyCheck

logger = logging.getLogger(__name__)


async def _timed_check(
    name: str, probe: Callable[[], Awaitable[object]], timeout_seconds: float
) -> DependencyCheck:
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            await probe()
    except Exception as exc:
        logger.warning("Health check for %s failed: %s", name, exc.__class__.__name__)
        return DependencyCheck(status="down", latency_ms=None, error=exc.__class__.__name__)
    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return DependencyCheck(status="up", latency_ms=latency_ms)


async def check_postgres(engine: AsyncEngine, timeout_seconds: float) -> DependencyCheck:
    async def probe() -> None:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    return await _timed_check("postgres", probe, timeout_seconds)


async def check_redis(client: Redis, timeout_seconds: float) -> DependencyCheck:
    async def probe() -> None:
        await client.ping()

    return await _timed_check("redis", probe, timeout_seconds)
