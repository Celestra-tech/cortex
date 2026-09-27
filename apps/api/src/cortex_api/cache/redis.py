from fastapi.requests import HTTPConnection
from redis.asyncio import Redis

from cortex_api.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        health_check_interval=30,
    )


def get_redis(request: HTTPConnection) -> Redis:
    client: Redis = request.app.state.redis
    return client
