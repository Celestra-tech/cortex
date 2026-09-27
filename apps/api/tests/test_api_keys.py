import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.core.security import hash_api_key
from cortex_api.models.api_key import ApiKey, ApiKeyRole
from cortex_api.models.audit_log import AuditLog
from cortex_api.models.organization import Organization
from cortex_api.repositories.api_key import ApiKeyRepository
from cortex_api.services.api_keys import ApiKeyService

from .conftest import OrganizationFactory

pytestmark = [pytest.mark.database, pytest.mark.redis]

ADMIN_TOKEN = "t" * 40


def bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


@pytest.fixture
async def admin_key(session: AsyncSession, organization: Organization) -> str:
    issued = await ApiKeyService(session).issue(organization.id, name="root", role=ApiKeyRole.ADMIN)
    return issued.secret


@pytest.fixture
async def member_key(session: AsyncSession, organization: Organization) -> str:
    return (await ApiKeyService(session).issue(organization.id, name="app")).secret


async def audit_actions(session: AsyncSession, organization: Organization) -> list[str]:
    rows = await session.execute(
        select(AuditLog.action)
        .where(AuditLog.organization_id == organization.id)
        .order_by(AuditLog.created_at, AuditLog.id)
    )
    return list(rows.scalars())


class TestLifecycle:
    async def test_create_returns_the_secret_once_and_stores_only_its_hash(
        self, api: httpx2.AsyncClient, admin_key: str, session: AsyncSession
    ) -> None:
        response = await api.post(
            "/v1/api-keys",
            json={"name": "backend", "role": "member", "expires_in_days": 30},
            headers=bearer(admin_key),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        secret = body["secret"]
        assert secret.startswith("ctx_") and body["prefix"] == secret[:12]
        assert body["role"] == "member" and body["status"] == "active"
        assert body["expires_at"] is not None

        row = await ApiKeyRepository(session).get(uuid.UUID(body["id"]))
        assert row is not None
        assert row.key_hash == hash_api_key(secret)
        stored = [str(value) for value in vars(row).values()]
        assert not any(secret in value for value in stored)

        listed = await api.get("/v1/api-keys", headers=bearer(admin_key))
        assert "secret" not in listed.json()["items"][0]
        # The new key works.
        assert (await api.get("/v1/organization", headers=bearer(secret))).status_code == 200

    async def test_list_newest_first_and_optionally_with_revoked(
        self, api: httpx2.AsyncClient, admin_key: str, member_key: str, session: AsyncSession
    ) -> None:
        listed = (await api.get("/v1/api-keys", headers=bearer(admin_key))).json()["items"]
        assert [k["name"] for k in listed] == ["app", "root"]

        member = await ApiKeyRepository(session).get_by_hash(hash_api_key(member_key))
        assert member is not None
        assert (await api.delete(f"/v1/api-keys/{member.id}", headers=bearer(admin_key))).json()[
            "status"
        ] == "revoked"

        active = (await api.get("/v1/api-keys", headers=bearer(admin_key))).json()["items"]
        everything = (
            await api.get("/v1/api-keys?include_revoked=true", headers=bearer(admin_key))
        ).json()["items"]
        assert [k["name"] for k in active] == ["root"]
        assert {k["name"]: k["status"] for k in everything} == {"app": "revoked", "root": "active"}

    async def test_revoked_keys_stop_working(
        self, api: httpx2.AsyncClient, admin_key: str, member_key: str, session: AsyncSession
    ) -> None:
        member = await ApiKeyRepository(session).get_by_hash(hash_api_key(member_key))
        assert member is not None
        response = await api.delete(f"/v1/api-keys/{member.id}", headers=bearer(admin_key))
        assert response.status_code == 200
        assert response.json()["revoked_at"] is not None
        assert (await api.get("/v1/organization", headers=bearer(member_key))).status_code == 401

    async def test_rotation_with_grace_keeps_the_old_key_until_it_lapses(
        self, api: httpx2.AsyncClient, admin_key: str, member_key: str, session: AsyncSession
    ) -> None:
        old = await ApiKeyRepository(session).get_by_hash(hash_api_key(member_key))
        assert old is not None
        response = await api.post(
            f"/v1/api-keys/{old.id}/rotate",
            json={"grace_period_seconds": 600},
            headers=bearer(admin_key),
        )
        assert response.status_code == 201, response.text
        new = response.json()
        assert new["name"] == "app" and new["role"] == "member"
        assert new["rotated_from_id"] == str(old.id)
        assert new["secret"] != member_key

        assert old.expires_at is not None
        assert timedelta(seconds=590) < old.expires_at - datetime.now(UTC) <= timedelta(seconds=600)
        assert (await api.get("/v1/organization", headers=bearer(member_key))).status_code == 200
        assert (await api.get("/v1/organization", headers=bearer(new["secret"]))).status_code == 200

    async def test_rotation_without_grace_revokes_immediately(
        self, api: httpx2.AsyncClient, admin_key: str, member_key: str, session: AsyncSession
    ) -> None:
        old = await ApiKeyRepository(session).get_by_hash(hash_api_key(member_key))
        assert old is not None
        response = await api.post(
            f"/v1/api-keys/{old.id}/rotate",
            json={"grace_period_seconds": 0},
            headers=bearer(admin_key),
        )
        assert response.status_code == 201
        assert (await api.get("/v1/organization", headers=bearer(member_key))).status_code == 401

    async def test_every_change_is_audited_without_secrets(
        self,
        api: httpx2.AsyncClient,
        admin_key: str,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        created = (
            await api.post("/v1/api-keys", json={"name": "ci"}, headers=bearer(admin_key))
        ).json()
        await api.post(f"/v1/api-keys/{created['id']}/rotate", headers=bearer(admin_key))
        await api.delete(f"/v1/api-keys/{created['id']}", headers=bearer(admin_key))

        assert await audit_actions(session, organization) == [
            "api_key.created",  # the admin fixture
            "api_key.created",
            "api_key.created",  # the replacement
            "api_key.rotated",
            "api_key.revoked",
        ]
        rows = (await session.execute(select(AuditLog))).scalars().all()
        assert all(created["secret"] not in str(row.metadata_) for row in rows)
        admin = await ApiKeyRepository(session).get_by_hash(hash_api_key(admin_key))
        assert admin is not None
        assert rows[-1].metadata_["actor_api_key_id"] == str(admin.id)


class TestAuthorization:
    async def test_member_keys_cannot_manage_keys(
        self, api: httpx2.AsyncClient, member_key: str
    ) -> None:
        response = await api.get("/v1/api-keys", headers=bearer(member_key))
        assert response.status_code == 403
        assert "admin" in response.json()["detail"]
        created = await api.post("/v1/api-keys", json={"name": "x"}, headers=bearer(member_key))
        assert created.status_code == 403

    async def test_member_keys_still_reach_tenant_data(
        self, api: httpx2.AsyncClient, member_key: str
    ) -> None:
        assert (await api.get("/v1/conversations", headers=bearer(member_key))).status_code == 200

    async def test_keys_of_other_organizations_are_invisible(
        self,
        api: httpx2.AsyncClient,
        admin_key: str,
        session: AsyncSession,
        organization_factory: OrganizationFactory,
    ) -> None:
        other = await organization_factory()
        foreign = (await ApiKeyService(session).issue(other.id, name="theirs")).api_key
        for request in (
            api.get(f"/v1/api-keys/{foreign.id}", headers=bearer(admin_key)),
            api.post(f"/v1/api-keys/{foreign.id}/rotate", headers=bearer(admin_key)),
            api.delete(f"/v1/api-keys/{foreign.id}", headers=bearer(admin_key)),
        ):
            assert (await request).status_code == 404
        listed = (await api.get("/v1/api-keys", headers=bearer(admin_key))).json()["items"]
        assert str(foreign.id) not in {k["id"] for k in listed}
        assert foreign.deleted_at is None

    async def test_the_last_admin_key_cannot_be_revoked(
        self, api: httpx2.AsyncClient, admin_key: str, session: AsyncSession
    ) -> None:
        admin = await ApiKeyRepository(session).get_by_hash(hash_api_key(admin_key))
        assert admin is not None
        response = await api.delete(f"/v1/api-keys/{admin.id}", headers=bearer(admin_key))
        assert response.status_code == 409
        assert "last usable admin key" in response.json()["detail"]

        second = (
            await api.post(
                "/v1/api-keys", json={"name": "backup", "role": "admin"}, headers=bearer(admin_key)
            )
        ).json()
        response = await api.delete(f"/v1/api-keys/{admin.id}", headers=bearer(admin_key))
        assert response.status_code == 200
        assert (await api.get("/v1/api-keys", headers=bearer(second["secret"]))).status_code == 200

    async def test_expired_keys_are_rejected(
        self, api: httpx2.AsyncClient, member_key: str, session: AsyncSession
    ) -> None:
        key = await ApiKeyRepository(session).get_by_hash(hash_api_key(member_key))
        assert key is not None
        await ApiKeyRepository(session).update(
            key, expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        response = await api.get("/v1/organization", headers=bearer(member_key))
        assert response.status_code == 401
        assert response.json()["detail"] == "API key has expired"


class TestLastUsed:
    async def test_use_is_recorded_and_throttled(
        self, api: httpx2.AsyncClient, member_key: str, session: AsyncSession
    ) -> None:
        repository = ApiKeyRepository(session)
        key = await repository.get_by_hash(hash_api_key(member_key))
        assert key is not None and key.last_used_at is None

        await api.get("/v1/organization", headers=bearer(member_key))
        first = key.last_used_at
        assert first is not None

        await api.get("/v1/organization", headers=bearer(member_key))
        assert key.last_used_at == first  # within the 60-second interval

        await repository.update(key, last_used_at=first - timedelta(minutes=5))
        await api.get("/v1/organization", headers=bearer(member_key))
        assert key.last_used_at is not None and key.last_used_at > first - timedelta(minutes=5)


class TestAdminBootstrap:
    @pytest.fixture
    def app_settings(self, redis_key_prefix: str) -> Settings:
        return Settings(
            env="test",
            admin_token=ADMIN_TOKEN,
            memory_session_key_prefix=redis_key_prefix,
            knowledge_cache_key_prefix=redis_key_prefix,
            rate_limit_key_prefix=redis_key_prefix,
            events_key_prefix=redis_key_prefix,
            _env_file=None,
        )

    async def test_creates_an_organization_with_its_first_admin_key(
        self, api: httpx2.AsyncClient, session: AsyncSession
    ) -> None:
        slug = f"acme-{uuid.uuid4().hex[:8]}"
        response = await api.post(
            "/v1/admin/organizations",
            json={"name": "Acme", "slug": slug},
            headers=bearer(ADMIN_TOKEN),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["organization"]["slug"] == slug
        assert body["api_key"]["role"] == "admin"

        secret = body["api_key"]["secret"]
        me = await api.get("/v1/organization", headers=bearer(secret))
        assert me.json()["id"] == body["organization"]["id"]
        assert (await api.get("/v1/api-keys", headers=bearer(secret))).status_code == 200

        again = await api.post(
            "/v1/admin/organizations",
            json={"name": "Acme 2", "slug": slug},
            headers=bearer(ADMIN_TOKEN),
        )
        assert again.status_code == 409

    @pytest.mark.parametrize("authorization", [None, "Bearer wrong", f"Basic {ADMIN_TOKEN}"])
    async def test_requires_the_admin_token(
        self, api: httpx2.AsyncClient, authorization: str | None
    ) -> None:
        headers = {"Authorization": authorization} if authorization else {}
        response = await api.post(
            "/v1/admin/organizations", json={"name": "A", "slug": "aa"}, headers=headers
        )
        assert response.status_code == 401

    async def test_an_api_key_is_not_an_admin_token(
        self, api: httpx2.AsyncClient, admin_key: str
    ) -> None:
        response = await api.post(
            "/v1/admin/organizations", json={"name": "A", "slug": "aa"}, headers=bearer(admin_key)
        )
        assert response.status_code == 401


async def test_admin_endpoints_are_hidden_without_a_token(
    api: httpx2.AsyncClient,
) -> None:
    response = await api.post(
        "/v1/admin/organizations", json={"name": "A", "slug": "aa"}, headers=bearer("anything")
    )
    assert response.status_code == 404


async def test_header_only_development_mode_acts_as_admin(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.post("/v1/api-keys", json={"name": "bootstrap"}, headers=headers)
    assert response.status_code == 201
    assert response.json()["role"] == "member"


async def test_legacy_rows_default_to_member_in_the_orm(
    session: AsyncSession, organization: Organization
) -> None:
    row = await ApiKeyRepository(session).create(
        organization_id=organization.id, name="raw", key_hash="b" * 64
    )
    assert isinstance(row, ApiKey) and row.role == "member" and row.prefix is None
