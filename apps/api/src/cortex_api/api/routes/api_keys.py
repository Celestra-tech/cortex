import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, status

from cortex_api.api.deps import AdminDep
from cortex_api.database.session import DbSession
from cortex_api.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyListResponse,
    ApiKeyRead,
    ApiKeyRotate,
)
from cortex_api.services.api_keys import ApiKeyService, IssuedKey

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


def _expiry(days: int | None) -> datetime | None:
    return None if days is None else datetime.now(UTC) + timedelta(days=days)


def _created(issued: IssuedKey) -> ApiKeyCreated:
    return ApiKeyCreated(**ApiKeyRead.of(issued.api_key).model_dump(), secret=issued.secret)


@router.get("", response_model=ApiKeyListResponse)
async def list_api_keys(
    admin: AdminDep,
    session: DbSession,
    include_revoked: Annotated[bool, Query()] = False,
) -> ApiKeyListResponse:
    """The organization's keys, newest first. Secrets are never returned."""
    keys = await ApiKeyService(session).list(admin.organization.id, include_revoked=include_revoked)
    now = datetime.now(UTC)
    return ApiKeyListResponse(items=[ApiKeyRead.of(key, now) for key in keys])


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(body: ApiKeyCreate, admin: AdminDep, session: DbSession) -> ApiKeyCreated:
    """Mint a key. The `secret` in the response is shown only this once."""
    issued = await ApiKeyService(session).issue(
        admin.organization.id,
        name=body.name,
        role=body.role,
        expires_at=_expiry(body.expires_in_days),
        actor_key_id=admin.api_key_id,
    )
    await session.commit()
    return _created(issued)


@router.get("/{key_id}", response_model=ApiKeyRead)
async def get_api_key(key_id: uuid.UUID, admin: AdminDep, session: DbSession) -> ApiKeyRead:
    return ApiKeyRead.of(await ApiKeyService(session).get(admin.organization.id, key_id))


@router.post("/{key_id}/rotate", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def rotate_api_key(
    key_id: uuid.UUID, admin: AdminDep, session: DbSession, body: ApiKeyRotate | None = None
) -> ApiKeyCreated:
    """Replace a key: same name and role, new secret. The old key lapses after the grace period."""
    body = body or ApiKeyRotate()
    issued = await ApiKeyService(session).rotate(
        admin.organization.id,
        key_id,
        grace_period=timedelta(seconds=body.grace_period_seconds),
        expires_at=_expiry(body.expires_in_days),
        actor_key_id=admin.api_key_id,
    )
    await session.commit()
    return _created(issued)


@router.delete("/{key_id}", response_model=ApiKeyRead)
async def revoke_api_key(key_id: uuid.UUID, admin: AdminDep, session: DbSession) -> ApiKeyRead:
    """Revoke immediately. The organization's last usable admin key cannot be revoked."""
    api_key = await ApiKeyService(session).revoke(
        admin.organization.id, key_id, actor_key_id=admin.api_key_id
    )
    await session.commit()
    return ApiKeyRead.of(api_key)
