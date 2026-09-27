"""Organization API key lifecycle: issue, list, rotate, revoke. Every change is audited.

Secrets exist only in the `IssuedKey` returned to the caller; the database
holds their SHA-256 digests and a short display prefix.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.security import display_prefix, generate_api_key, hash_api_key
from cortex_api.models.api_key import ApiKey, ApiKeyRole
from cortex_api.repositories.api_key import ApiKeyRepository
from cortex_api.repositories.audit_log import AuditLogRepository
from cortex_api.repositories.base import NotFoundError


@dataclass(frozen=True, slots=True)
class IssuedKey:
    api_key: ApiKey
    secret: str
    """Shown once. Never logged or persisted."""


class LastAdminKeyError(Exception):
    status_code = 409

    def __init__(self) -> None:
        super().__init__(
            "Refusing to revoke the organization's last usable admin key; "
            "create another admin key first"
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


class ApiKeyService:
    """Flushes but never commits: the caller owns the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.keys = ApiKeyRepository(session)
        self.audit = AuditLogRepository(session)

    async def issue(
        self,
        organization_id: uuid.UUID,
        *,
        name: str,
        role: ApiKeyRole = ApiKeyRole.MEMBER,
        expires_at: datetime | None = None,
        actor_key_id: uuid.UUID | None = None,
        rotated_from_id: uuid.UUID | None = None,
    ) -> IssuedKey:
        secret = generate_api_key()
        api_key = await self.keys.create(
            organization_id=organization_id,
            name=name,
            key_hash=hash_api_key(secret),
            prefix=display_prefix(secret),
            role=role.value,
            expires_at=expires_at,
            rotated_from_id=rotated_from_id,
        )
        await self._record(
            "api_key.created",
            api_key,
            actor_key_id,
            name=name,
            role=role.value,
            expires_at=expires_at,
            rotated_from_id=rotated_from_id,
        )
        return IssuedKey(api_key, secret)

    async def list(
        self, organization_id: uuid.UUID, *, include_revoked: bool = False
    ) -> Sequence[ApiKey]:
        return await self.keys.list_for_organization(
            organization_id, include_revoked=include_revoked
        )

    async def get(self, organization_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
        api_key = await self.keys.get_for_organization(organization_id, key_id)
        if api_key is None:
            raise NotFoundError(ApiKey, key_id)
        return api_key

    async def rotate(
        self,
        organization_id: uuid.UUID,
        key_id: uuid.UUID,
        *,
        grace_period: timedelta,
        expires_at: datetime | None = None,
        actor_key_id: uuid.UUID | None = None,
    ) -> IssuedKey:
        """Issue a replacement with the same name and role; retire the old key.

        With a grace period the old key keeps working until it lapses, so
        deployments can roll over without downtime; otherwise it is revoked now.
        """
        old = await self.get(organization_id, key_id)
        issued = await self.issue(
            organization_id,
            name=old.name,
            role=ApiKeyRole(old.role),
            expires_at=expires_at,
            actor_key_id=actor_key_id,
            rotated_from_id=old.id,
        )
        now = _utcnow()
        if grace_period <= timedelta(0):
            await self.keys.delete(old)
        else:
            cutoff = now + grace_period
            if old.expires_at is None or old.expires_at > cutoff:
                await self.keys.update(old, expires_at=cutoff)
        await self._record(
            "api_key.rotated",
            old,
            actor_key_id,
            replacement_id=issued.api_key.id,
            valid_until=old.deleted_at or old.expires_at,
        )
        return issued

    async def revoke(
        self,
        organization_id: uuid.UUID,
        key_id: uuid.UUID,
        *,
        actor_key_id: uuid.UUID | None = None,
    ) -> ApiKey:
        api_key = await self.get(organization_id, key_id)
        now = _utcnow()
        if (
            api_key.role == ApiKeyRole.ADMIN
            and not api_key.is_expired(now)
            and await self.keys.count_usable_admins(organization_id, now, excluding=api_key.id) == 0
        ):
            raise LastAdminKeyError
        await self.keys.delete(api_key)
        await self._record("api_key.revoked", api_key, actor_key_id)
        return api_key

    async def _record(
        self, action: str, api_key: ApiKey, actor_key_id: uuid.UUID | None, **details: Any
    ) -> None:
        metadata = {key: _jsonable(value) for key, value in details.items() if value is not None}
        if actor_key_id is not None:
            metadata["actor_api_key_id"] = str(actor_key_id)
        await self.audit.create(
            organization_id=api_key.organization_id,
            action=action,
            resource=f"api_key:{api_key.id}",
            metadata_=metadata,
        )
