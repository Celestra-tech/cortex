import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm.attributes import set_committed_value

from cortex_api.models.api_key import ApiKey, ApiKeyRole
from cortex_api.repositories.base import BaseRepository


class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        """Active keys only; revoked (soft-deleted) keys never match."""
        result = await self.session.execute(self.select().where(ApiKey.key_hash == key_hash))
        return result.scalar_one_or_none()

    async def get_for_organization(
        self, organization_id: uuid.UUID, key_id: uuid.UUID, *, include_revoked: bool = False
    ) -> ApiKey | None:
        statement = self.select(include_deleted=include_revoked).where(
            ApiKey.id == key_id, ApiKey.organization_id == organization_id
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, include_revoked: bool = False
    ) -> Sequence[ApiKey]:
        """Newest first."""
        statement = (
            self.select(include_deleted=include_revoked)
            .where(ApiKey.organization_id == organization_id)
            .order_by(ApiKey.id.desc())
            .limit(self.max_limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def count_usable_admins(
        self, organization_id: uuid.UUID, now: datetime, *, excluding: uuid.UUID | None = None
    ) -> int:
        """Admin keys that are neither revoked nor expired."""
        statement = (
            select(func.count())
            .select_from(ApiKey)
            .where(
                ApiKey.organization_id == organization_id,
                ApiKey.role == ApiKeyRole.ADMIN.value,
                ApiKey.deleted_at.is_(None),
                or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
            )
        )
        if excluding is not None:
            statement = statement.where(ApiKey.id != excluding)
        result = await self.session.execute(statement)
        return result.scalar_one()

    async def touch(self, api_key: ApiKey, now: datetime) -> None:
        """Record use with a single-column UPDATE rather than a full ORM flush."""
        await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(last_used_at=now)
            .execution_options(synchronize_session=False)
        )
        set_committed_value(api_key, "last_used_at", now)
