from collections.abc import AsyncIterator

import httpx2
import pytest
from fastapi import FastAPI, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.core.http import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from cortex_api.core.rate_limit import RateLimiter
from cortex_api.models.organization import Organization
from cortex_api.repositories.organization import OrganizationRepository

from .conftest import OrganizationFactory


def echo_app(**middleware_options: int) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"received": len(await request.body())}

    @app.get("/docs/page")
    async def docs() -> dict[str, str]:
        return {}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=middleware_options.get("max", 1024))
    app.add_middleware(SecurityHeadersMiddleware, hsts_seconds=middleware_options.get("hsts"))
    return app


@pytest.fixture
async def echo() -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=echo_app(max=1024, hsts=31536000))
    async with httpx2.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


class TestSecurityHeaders:
    async def test_api_responses_are_hardened(self, echo: httpx2.AsyncClient) -> None:
        response = await echo.post("/v1/echo", content=b"hi")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["content-security-policy"].startswith("default-src 'none'")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["strict-transport-security"] == (
            "max-age=31536000; includeSubDomains"
        )

    async def test_docs_are_exempt_from_the_csp(self, echo: httpx2.AsyncClient) -> None:
        response = await echo.get("/docs/page")
        assert "content-security-policy" not in response.headers
        assert "cache-control" not in response.headers

    async def test_hsts_is_off_unless_configured(self) -> None:
        transport = httpx2.ASGITransport(app=echo_app())
        async with httpx2.AsyncClient(transport=transport, base_url="http://t") as client:
            response = await client.post("/v1/echo", content=b"")
        assert "strict-transport-security" not in response.headers


class TestBodyLimit:
    async def test_bodies_within_the_limit_pass(self, echo: httpx2.AsyncClient) -> None:
        response = await echo.post("/v1/echo", content=b"x" * 1024)
        assert response.json() == {"received": 1024}

    async def test_a_declared_oversize_body_is_refused_unread(
        self, echo: httpx2.AsyncClient
    ) -> None:
        response = await echo.post("/v1/echo", content=b"x" * 1025)
        assert response.status_code == 413
        assert "1024-byte limit" in response.json()["detail"]

    async def test_a_streamed_oversize_body_is_refused(self, echo: httpx2.AsyncClient) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            for _ in range(5):
                yield b"x" * 300

        response = await echo.post("/v1/echo", content=chunks())
        assert response.status_code == 413

    async def test_a_malformed_length_is_rejected(self) -> None:
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, object]) -> None:
            sent.append(message)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/echo",
            "headers": [(b"content-length", b"nope")],
        }
        await BodySizeLimitMiddleware(echo_app(), max_bytes=10)(scope, receive, send)
        assert sent[0]["status"] == 400


@pytest.mark.database
@pytest.mark.redis
class TestCors:
    @pytest.fixture
    def app_settings(self, redis_key_prefix: str) -> Settings:
        return Settings(
            env="test",
            api_cors_origins="https://app.celestra.ai",
            rate_limit_key_prefix=redis_key_prefix,
            _env_file=None,
        )

    async def test_preflight_allows_the_sdk_headers(self, api: httpx2.AsyncClient) -> None:
        response = await api.options(
            "/v1/chat/completions",
            headers={
                "Origin": "https://app.celestra.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,x-request-id",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://app.celestra.ai"
        assert "access-control-allow-credentials" not in response.headers
        assert response.headers["x-request-id"]

    async def test_unknown_origins_get_no_cors_grant(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    async def test_rate_limit_headers_are_exposed(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        response = await api.get(
            "/v1/organization", headers={**headers, "Origin": "https://app.celestra.ai"}
        )
        exposed = response.headers["access-control-expose-headers"]
        assert "X-RateLimit-Remaining" in exposed and "X-Request-ID" in exposed


@pytest.mark.database
@pytest.mark.redis
class TestRateLimiting:
    @pytest.fixture
    def app_settings(self, redis_key_prefix: str) -> Settings:
        return Settings(
            env="test",
            rate_limit_requests_per_minute=3,
            memory_session_key_prefix=redis_key_prefix,
            knowledge_cache_key_prefix=redis_key_prefix,
            rate_limit_key_prefix=redis_key_prefix,
            events_key_prefix=redis_key_prefix,
            _env_file=None,
        )

    async def test_requests_over_the_limit_get_429(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        remaining = []
        for _ in range(3):
            response = await api.get("/v1/organization", headers=headers)
            assert response.status_code == 200
            assert response.headers["x-ratelimit-limit"] == "3"
            remaining.append(response.headers["x-ratelimit-remaining"])
        assert remaining == ["2", "1", "0"]

        blocked = await api.get("/v1/organization", headers=headers)
        assert blocked.status_code == 429
        assert 1 <= int(blocked.headers["retry-after"]) <= 60
        assert blocked.headers["x-ratelimit-remaining"] == "0"
        assert "3 requests per minute" in blocked.json()["detail"]

    async def test_limits_are_per_tenant(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        organization_factory: OrganizationFactory,
    ) -> None:
        for _ in range(4):
            await api.get("/v1/organization", headers=headers)
        other = await organization_factory()
        response = await api.get("/v1/organization", headers={"X-Organization-ID": str(other.id)})
        assert response.status_code == 200

    async def test_an_organization_override_raises_the_limit(
        self, api: httpx2.AsyncClient, session: AsyncSession, organization: Organization
    ) -> None:
        await OrganizationRepository(session).update(
            organization, settings={"rate_limit_per_minute": 10}
        )
        headers = {"X-Organization-ID": str(organization.id)}
        statuses = [
            (await api.get("/v1/organization", headers=headers)).status_code for _ in range(5)
        ]
        assert statuses == [200] * 5

    async def test_health_checks_are_never_limited(self, api: httpx2.AsyncClient) -> None:
        statuses = {(await api.get("/health")).status_code for _ in range(6)}
        assert statuses == {200}


@pytest.mark.redis
async def test_the_limiter_counts_per_window(redis: Redis, redis_key_prefix: str) -> None:
    now = [120.0]
    limiter = RateLimiter(redis, key_prefix=redis_key_prefix, clock=lambda: now[0])
    first = await limiter.hit("key:a", 2)
    assert first is not None and (first.remaining, first.reset_seconds) == (1, 60)
    await limiter.hit("key:a", 2)
    third = await limiter.hit("key:a", 2)
    assert third is not None and third.exceeded
    now[0] = 180.5  # next window
    fresh = await limiter.hit("key:a", 2)
    assert fresh is not None and not fresh.exceeded and fresh.reset_seconds == 60


async def test_the_limiter_fails_open_without_redis() -> None:
    unreachable = Redis.from_url("redis://127.0.0.1:1/15", socket_connect_timeout=0.2)
    try:
        assert await RateLimiter(unreachable).hit("key:a", 1) is None
    finally:
        await unreachable.aclose()
