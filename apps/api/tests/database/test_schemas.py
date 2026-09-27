import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models import UserRole
from cortex_api.repositories import OrganizationRepository, UserRepository
from cortex_api.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from cortex_api.schemas.user import UserCreate, UserRead, UserUpdate


class TestOrganizationSchemas:
    def test_create_trims_name_and_accepts_valid_slug(self) -> None:
        body = OrganizationCreate(name="  Celestra Labs ", slug="celestra-labs")
        assert body.name == "Celestra Labs"

    @pytest.mark.parametrize(
        "slug", ["", "Upper", "-leading", "trailing-", "under_score", "a" * 64]
    )
    def test_create_rejects_slugs_the_database_would_reject(self, slug: str) -> None:
        with pytest.raises(ValidationError):
            OrganizationCreate(name="Acme", slug=slug)

    def test_create_rejects_blank_name(self) -> None:
        with pytest.raises(ValidationError):
            OrganizationCreate(name="   ", slug="acme")

    def test_update_changes_include_only_provided_fields(self) -> None:
        assert OrganizationUpdate().changes() == {}
        assert OrganizationUpdate(name="Acme").changes() == {"name": "Acme"}
        assert OrganizationUpdate(name=None, settings={"a": 1}).changes() == {"settings": {"a": 1}}


class TestUserSchemas:
    def test_create_normalizes_email_and_defaults_role(self) -> None:
        body = UserCreate(email="Ada.Lovelace@Example.COM", full_name=" Ada ")
        assert body.email == "ada.lovelace@example.com"
        assert body.full_name == "Ada"
        assert body.role is UserRole.MEMBER

    @pytest.mark.parametrize("email", ["", "not-an-email", "ada@", "@example.com"])
    def test_create_rejects_invalid_email(self, email: str) -> None:
        with pytest.raises(ValidationError):
            UserCreate(email=email)

    def test_create_rejects_unknown_role(self) -> None:
        with pytest.raises(ValidationError):
            UserCreate.model_validate({"email": "ada@example.com", "role": "superuser"})

    def test_update_can_clear_full_name_but_not_required_fields(self) -> None:
        assert UserUpdate().changes() == {}
        assert UserUpdate(full_name=None).changes() == {"full_name": None}
        assert UserUpdate(email=None, role=UserRole.ADMIN).changes() == {"role": UserRole.ADMIN}


@pytest.mark.database
async def test_schema_driven_organization_crud(session: AsyncSession) -> None:
    organizations = OrganizationRepository(session)

    body = OrganizationCreate(name="Celestra Labs", slug="celestra-labs")
    created = await organizations.create(**body.model_dump())
    read = OrganizationRead.model_validate(created)
    assert read.id == created.id
    assert read.slug == "celestra-labs"
    assert read.settings == {}

    patch = OrganizationUpdate(name="Celestra", settings={"routing": {"strategy": "cost"}})
    await organizations.update(created, **patch.changes())
    reread = OrganizationRead.model_validate(await organizations.get_or_raise(created.id))
    assert reread.name == "Celestra"
    assert reread.slug == "celestra-labs"
    assert reread.settings == {"routing": {"strategy": "cost"}}

    await organizations.delete(created)
    assert await organizations.get(created.id) is None


@pytest.mark.database
async def test_schema_driven_user_crud(session: AsyncSession) -> None:
    organization = await OrganizationRepository(session).create(name="Acme", slug="acme")
    users = UserRepository(session)

    body = UserCreate(email="Ada@Example.com", full_name="Ada Lovelace", role=UserRole.ADMIN)
    user = await users.create(organization_id=organization.id, **body.model_dump())
    read = UserRead.model_validate(user)
    assert read.organization_id == organization.id
    assert read.email == "ada@example.com"
    assert read.role is UserRole.ADMIN

    await users.update(user, **UserUpdate(full_name=None, role=UserRole.VIEWER).changes())
    reread = UserRead.model_validate(await users.get_or_raise(user.id))
    assert reread.full_name is None
    assert reread.role is UserRole.VIEWER
    assert reread.email == "ada@example.com"

    await users.delete(user)
    assert await users.get_by_email(organization.id, "ada@example.com") is None
