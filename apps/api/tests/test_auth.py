import uuid

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.core.config import Settings
from cortex_api.core.security import API_KEY_PREFIX, generate_api_key, hash_api_key
from cortex_api.models.organization import Organization
from cortex_api.repositories.api_key import ApiKeyRepository

from .conftest import OrganizationFactory

pytestmark = [pytest.mark.database, pytest.mark.redis]


@pytest.fixture
async def api_key(session: AsyncSession, organization: Organization) -> str:
    secret = generate_api_key()
    await ApiKeyRepository(session).create(
        organization_id=organization.id, name="test", key_hash=hash_api_key(secret)
    )
    return secret


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


def test_keys_are_prefixed_high_entropy_and_hashed() -> None:
    first, second = generate_api_key(), generate_api_key()
    assert first.startswith(API_KEY_PREFIX) and first != second
    assert len(first) > 40
    assert hash_api_key(first) == hash_api_key(first) != first
    assert len(hash_api_key(first)) == 64


class TestApiKeys:
    async def test_a_key_resolves_its_organization(
        self, api: httpx2.AsyncClient, api_key: str, organization: Organization
    ) -> None:
        response = await api.get("/v1/organization", headers=bearer(api_key))
        assert response.status_code == 200
        assert response.json()["id"] == str(organization.id)

    async def test_a_matching_organization_header_is_allowed(
        self, api: httpx2.AsyncClient, api_key: str, headers: dict[str, str]
    ) -> None:
        response = await api.get("/v1/organization", headers={**bearer(api_key), **headers})
        assert response.status_code == 200

    async def test_a_key_cannot_reach_another_organization(
        self, api: httpx2.AsyncClient, api_key: str, organization_factory: OrganizationFactory
    ) -> None:
        other = await organization_factory()
        response = await api.get(
            "/v1/organization",
            headers={**bearer(api_key), ORGANIZATION_HEADER: str(other.id)},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "authorization",
        ["Bearer ctx_not-a-real-key", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   "],
    )
    async def test_bad_credentials_are_rejected(
        self, api: httpx2.AsyncClient, authorization: str, headers: dict[str, str]
    ) -> None:
        # The organization header must not rescue a bad key.
        response = await api.get(
            "/v1/organization", headers={"Authorization": authorization, **headers}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_revoked_keys_stop_working(
        self, api: httpx2.AsyncClient, api_key: str, session: AsyncSession
    ) -> None:
        repository = ApiKeyRepository(session)
        row = await repository.get_by_hash(hash_api_key(api_key))
        assert row is not None
        await repository.delete(row)
        assert (await api.get("/v1/organization", headers=bearer(api_key))).status_code == 401

    async def test_no_credentials_is_unauthenticated(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/v1/organization")
        assert response.status_code == 401
        assert "Authorization" in response.json()["detail"]


class TestRequiredApiKeys:
    @pytest.fixture
    def app_settings(self, redis_key_prefix: str) -> Settings:
        return Settings(
            env="test",
            auth_require_api_key=True,
            memory_session_key_prefix=redis_key_prefix,
            knowledge_cache_key_prefix=redis_key_prefix,
            rate_limit_key_prefix=redis_key_prefix,
            events_key_prefix=redis_key_prefix,
            _env_file=None,
        )

    async def test_the_organization_header_alone_is_refused(
        self, api: httpx2.AsyncClient, headers: dict[str, str], api_key: str
    ) -> None:
        assert (await api.get("/v1/organization", headers=headers)).status_code == 401
        assert (await api.get("/v1/organization", headers=bearer(api_key))).status_code == 200


class TestRequestIds:
    async def test_a_valid_client_id_is_echoed(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/health", headers={"X-Request-ID": "req_abc-123"})
        assert response.headers["x-request-id"] == "req_abc-123"

    @pytest.mark.parametrize("supplied", [None, "has spaces", "x" * 129, "<script>"])
    async def test_missing_or_unsafe_ids_are_replaced(
        self, api: httpx2.AsyncClient, supplied: str | None
    ) -> None:
        headers = {"X-Request-ID": supplied} if supplied else {}
        response = await api.get("/health", headers=headers)
        generated = response.headers["x-request-id"]
        assert generated != supplied
        assert uuid.UUID(hex=generated).version == 7

    async def test_errors_carry_the_id(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/v1/organization", headers={"X-Request-ID": "trace-1"})
        assert response.status_code == 401
        assert response.headers["x-request-id"] == "trace-1"
