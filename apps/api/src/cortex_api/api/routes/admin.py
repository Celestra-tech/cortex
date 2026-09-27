"""Platform-operator endpoints, authenticated by `CORTEX_ADMIN_TOKEN` rather than an API key."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status

from cortex_api.api.deps import SettingsDep
from cortex_api.core.security import AuthenticationError, tokens_match
from cortex_api.database.session import DbSession
from cortex_api.models.api_key import ApiKeyRole
from cortex_api.repositories.organization import OrganizationRepository
from cortex_api.schemas.api_key import (
    ApiKeyCreated,
    ApiKeyRead,
    OrganizationBootstrap,
    OrganizationCreate,
)
from cortex_api.schemas.observatory import OrganizationRead
from cortex_api.services.api_keys import ApiKeyService


def require_admin_token(
    settings: SettingsDep, authorization: Annotated[str | None, Header()] = None
) -> None:
    if settings.admin_token is None:
        # Indistinguishable from a missing route, so the endpoint is not advertised.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not tokens_match(
        token.strip(), settings.admin_token.get_secret_value()
    ):
        raise AuthenticationError("A valid admin token is required")


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
    include_in_schema=False,
)


@router.post(
    "/organizations", response_model=OrganizationBootstrap, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    body: OrganizationCreate, session: DbSession
) -> OrganizationBootstrap:
    """Create a tenant with its first admin API key (the secret is returned once)."""
    organizations = OrganizationRepository(session)
    if await organizations.get_by_slug(body.slug) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Organization slug {body.slug!r} is taken")
    organization = await organizations.create(name=body.name, slug=body.slug)
    issued = await ApiKeyService(session).issue(
        organization.id, name=body.key_name, role=ApiKeyRole.ADMIN
    )
    await session.commit()
    return OrganizationBootstrap(
        organization=OrganizationRead.model_validate(organization),
        api_key=ApiKeyCreated(**ApiKeyRead.of(issued.api_key).model_dump(), secret=issued.secret),
    )
