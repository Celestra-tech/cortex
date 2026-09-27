from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models import AuditLog, UserRole
from cortex_api.repositories import (
    ApiKeyRepository,
    AuditLogRepository,
    NotFoundError,
    OrganizationRepository,
    UserRepository,
)

pytestmark = pytest.mark.database


async def test_organization_crud_with_soft_delete(session: AsyncSession) -> None:
    organizations = OrganizationRepository(session)

    created = await organizations.create(name="Celestra Labs", slug="celestra-labs")
    assert created.id.version == 7
    assert created.created_at.tzinfo is not None
    assert created.updated_at is not None
    assert created.deleted_at is None

    assert await organizations.get(created.id) is created
    assert await organizations.get_by_slug("celestra-labs") is created
    assert await organizations.count() == 1

    updated = await organizations.update(created, name="Celestra")
    assert updated.name == "Celestra"
    with pytest.raises(AttributeError):
        await organizations.update(created, not_a_column=True)

    await organizations.delete(created)
    assert created.is_deleted
    assert await organizations.get(created.id) is None
    assert await organizations.get(created.id, include_deleted=True) is created
    assert await organizations.count() == 0
    with pytest.raises(NotFoundError):
        await organizations.get_or_raise(created.id)

    await organizations.restore(created)
    assert await organizations.get(created.id) is created

    await organizations.delete(created)
    reused = await organizations.create(name="Celestra Again", slug="celestra-labs")
    assert [org.id for org in await organizations.list()] == [reused.id]

    await organizations.delete(reused, hard=True)
    assert await organizations.get(reused.id, include_deleted=True) is None


async def test_list_is_ordered_and_bounded(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    organizations = OrganizationRepository(session)
    created = [await organizations.create(name=f"Org {i}", slug=f"org-{i}") for i in range(5)]
    ids = [org.id for org in created]

    assert [org.id for org in await organizations.list()] == ids
    assert [org.id for org in await organizations.list(limit=2)] == ids[:2]
    assert [org.id for org in await organizations.list(limit=2, offset=2)] == ids[2:4]
    assert await organizations.list(offset=5) == []

    monkeypatch.setattr(OrganizationRepository, "max_limit", 3)
    assert len(await organizations.list(limit=10_000)) == 3

    await organizations.delete(created[0])
    assert len(await organizations.list(include_deleted=True)) == 3
    assert await organizations.count(include_deleted=True) == 5
    assert await organizations.count() == 4


async def test_users_and_api_keys_are_scoped_to_organization(session: AsyncSession) -> None:
    organization = await OrganizationRepository(session).create(name="Acme", slug="acme")
    users = UserRepository(session)
    api_keys = ApiKeyRepository(session)

    user = await users.create(organization_id=organization.id, email="  Ada@Example.COM ")
    assert user.email == "ada@example.com"
    assert user.role is UserRole.MEMBER
    assert await users.get_by_email(organization.id, "ADA@example.com") is user
    assert await users.list_by_organization(organization.id) == [user]

    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await users.create(organization_id=organization.id, email="ada@example.com")

    key = await api_keys.create(organization_id=organization.id, name="CI", key_hash="a" * 64)
    assert key.last_used_at is None
    assert await api_keys.get_by_hash("a" * 64) is key

    assert key.role == "member"
    await api_keys.touch(key, datetime.now(UTC))
    assert key.last_used_at is not None
    assert key not in session.dirty

    await api_keys.delete(key)
    assert await api_keys.get_by_hash("a" * 64) is None


async def test_audit_log_jsonb_metadata_is_queryable_and_append_only(
    session: AsyncSession,
) -> None:
    organization = await OrganizationRepository(session).create(name="Acme", slug="acme")
    actor = await UserRepository(session).create(
        organization_id=organization.id, email="owner@acme.io", role=UserRole.OWNER
    )
    audit_logs = AuditLogRepository(session)

    entry = await audit_logs.create(
        organization_id=organization.id,
        actor_id=actor.id,
        action="user.role_changed",
        resource=f"users/{actor.id}",
        metadata_={"ip": "10.0.0.1", "changes": {"role": ["member", "owner"]}},
    )
    await audit_logs.create(organization_id=organization.id, action="org.viewed", resource="org")

    matches = await session.scalars(
        select(AuditLog).where(AuditLog.metadata_.contains({"ip": "10.0.0.1"}))
    )
    assert matches.all() == [entry]
    assert len(await audit_logs.list_for_organization(organization.id)) == 2

    with pytest.raises(TypeError):
        await audit_logs.update(entry, action="tampered")
    with pytest.raises(TypeError):
        await audit_logs.delete(entry)
