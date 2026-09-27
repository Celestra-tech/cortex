from __future__ import annotations

from ._resource import AsyncAPIResource, SyncAPIResource
from ._transport import Call, RequestOptions
from .models import (
    DatabaseHealth,
    Health,
    Organization,
    ProvidersHealth,
    Readiness,
    RedisHealth,
)


def _organization(options: RequestOptions | None) -> Call:
    return Call(operation="system.organization", path="/v1/organization", options=options)


def _health(options: RequestOptions | None) -> Call:
    return Call(operation="system.health", path="/health", options=options)


def _probe(operation: str, path: str, options: RequestOptions | None) -> Call:
    # Probes answer 503 with a body when unhealthy; report it rather than retry.
    return Call(
        operation=operation,
        path=path,
        accept_statuses=frozenset({503}),
        options={"max_retries": 0, **(options or {})},
    )


class System(SyncAPIResource):
    """Who the credentials belong to, plus liveness and readiness probes."""

    def organization(self, *, request_options: RequestOptions | None = None) -> Organization:
        """The organization this client's credentials resolve to."""
        return self._transport.request(_organization(request_options), Organization)

    def health(self, *, request_options: RequestOptions | None = None) -> Health:
        return self._transport.request(_health(request_options), Health)

    def readiness(self, *, request_options: RequestOptions | None = None) -> Readiness:
        """Postgres and Redis checks. A degraded API returns normally with
        `status == "degraded"`."""
        call = _probe("system.readiness", "/health/ready", request_options)
        return self._transport.request(call, Readiness)

    def database_health(self, *, request_options: RequestOptions | None = None) -> DatabaseHealth:
        call = _probe("system.database_health", "/health/database", request_options)
        return self._transport.request(call, DatabaseHealth)

    def redis_health(self, *, request_options: RequestOptions | None = None) -> RedisHealth:
        call = _probe("system.redis_health", "/health/redis", request_options)
        return self._transport.request(call, RedisHealth)

    def providers_health(self, *, request_options: RequestOptions | None = None) -> ProvidersHealth:
        """Credentials and circuit-breaker state per provider; the API makes no provider calls."""
        call = _probe("system.providers_health", "/health/providers", request_options)
        return self._transport.request(call, ProvidersHealth)


class AsyncSystem(AsyncAPIResource):
    async def organization(self, *, request_options: RequestOptions | None = None) -> Organization:
        return await self._transport.request(_organization(request_options), Organization)

    async def health(self, *, request_options: RequestOptions | None = None) -> Health:
        return await self._transport.request(_health(request_options), Health)

    async def readiness(self, *, request_options: RequestOptions | None = None) -> Readiness:
        call = _probe("system.readiness", "/health/ready", request_options)
        return await self._transport.request(call, Readiness)

    async def database_health(
        self, *, request_options: RequestOptions | None = None
    ) -> DatabaseHealth:
        call = _probe("system.database_health", "/health/database", request_options)
        return await self._transport.request(call, DatabaseHealth)

    async def redis_health(self, *, request_options: RequestOptions | None = None) -> RedisHealth:
        call = _probe("system.redis_health", "/health/redis", request_options)
        return await self._transport.request(call, RedisHealth)

    async def providers_health(
        self, *, request_options: RequestOptions | None = None
    ) -> ProvidersHealth:
        call = _probe("system.providers_health", "/health/providers", request_options)
        return await self._transport.request(call, ProvidersHealth)
