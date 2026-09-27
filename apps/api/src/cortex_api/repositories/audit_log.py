import uuid
from collections.abc import Sequence
from typing import Any, NoReturn

from cortex_api.models.audit_log import AuditLog
from cortex_api.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """Append-only: updates and deletes are rejected."""

    model = AuditLog

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[AuditLog]:
        statement = (
            self.select()
            .where(AuditLog.organization_id == organization_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(min(limit, self.max_limit))
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def update(self, entity: AuditLog, **values: Any) -> NoReturn:
        raise TypeError("Audit logs are append-only")

    async def delete(self, entity: AuditLog, *, hard: bool = False) -> NoReturn:
        raise TypeError("Audit logs are append-only")
