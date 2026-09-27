"""Fixed-window request limits in Redis, shared by every API instance.

One INCR per request keeps the hot path to a single round trip. Windows are
aligned to the minute, so a client can burst up to twice the limit across a
boundary; that is acceptable for abuse protection, which is the goal here.
If Redis is unreachable the limiter fails open: availability beats strictness.
"""

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


@dataclass(frozen=True, slots=True)
class RateLimitStatus:
    limit: int
    remaining: int
    reset_seconds: int
    exceeded: bool

    def headers(self) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }


class RateLimitExceededError(Exception):
    """HTTP 429."""

    def __init__(self, status: RateLimitStatus) -> None:
        super().__init__(f"Rate limit of {status.limit} requests per minute exceeded")
        self.status = status


class RateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        key_prefix: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.redis = redis
        self.key_prefix = key_prefix
        self._clock = clock

    async def hit(self, bucket: str, limit: int) -> RateLimitStatus | None:
        """Count one request against `bucket`; None when Redis is unavailable."""
        now = self._clock()
        window = math.floor(now / WINDOW_SECONDS)
        key = f"{self.key_prefix}ratelimit:{bucket}:{window}"
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, WINDOW_SECONDS * 2)
                count, _ = await pipe.execute()
        except (RedisError, OSError) as exc:
            logger.warning("Rate limiter unavailable, allowing request: %s", exc)
            return None
        reset = max(1, math.ceil((window + 1) * WINDOW_SECONDS - now))
        return RateLimitStatus(
            limit=limit,
            remaining=max(0, limit - int(count)),
            reset_seconds=reset,
            exceeded=int(count) > limit,
        )


class RateLimitHeadersMiddleware:
    """Adds `X-RateLimit-*` to responses of requests that were counted.

    The limit is enforced during authentication (it depends on the tenant),
    which stores the status in the request state for this middleware to read.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status = scope.get("state", {}).get("rate_limit")
                if isinstance(status, RateLimitStatus):
                    headers = MutableHeaders(scope=message)
                    for name, value in status.headers().items():
                        headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
