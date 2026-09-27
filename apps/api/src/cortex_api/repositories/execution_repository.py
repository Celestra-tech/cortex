import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, NoReturn

from sqlalchemy import ColumnElement, Select, func, select

from cortex_api.models.model_execution import ModelExecution
from cortex_api.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class ExecutionFilters:
    provider: str | None = None
    model: str | None = None
    success: bool | None = None
    completion_id: uuid.UUID | None = None

    def clauses(self, organization_id: uuid.UUID) -> list[ColumnElement[bool]]:
        clauses = [ModelExecution.organization_id == organization_id]
        if self.provider is not None:
            clauses.append(ModelExecution.provider == self.provider)
        if self.model is not None:
            clauses.append(ModelExecution.model == self.model)
        if self.success is not None:
            clauses.append(ModelExecution.success.is_(self.success))
        if self.completion_id is not None:
            clauses.append(ModelExecution.completion_id == self.completion_id)
        return clauses


class ExecutionRepository(BaseRepository[ModelExecution]):
    """Append-only log of provider calls: updates and deletes are rejected."""

    model = ModelExecution
    default_limit = 50
    max_limit = 200

    async def record_many(self, executions: Sequence[ModelExecution]) -> list[ModelExecution]:
        self.session.add_all(executions)
        await self.session.flush()
        return list(executions)

    async def get_for_organization(
        self, organization_id: uuid.UUID, execution_id: uuid.UUID
    ) -> ModelExecution | None:
        statement = self.select().where(
            ModelExecution.id == execution_id,
            ModelExecution.organization_id == organization_id,
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        filters: ExecutionFilters | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[ModelExecution]:
        statement: Select[ModelExecution] = (
            self.select()
            .where(*(filters or ExecutionFilters()).clauses(organization_id))
            .order_by(ModelExecution.created_at.desc(), ModelExecution.id.desc())
            .limit(min(limit or self.default_limit, self.max_limit))
            .offset(offset)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def count_for_organization(
        self, organization_id: uuid.UUID, filters: ExecutionFilters | None = None
    ) -> int:
        statement = select(func.count()).where(
            *(filters or ExecutionFilters()).clauses(organization_id)
        )
        return (await self.session.execute(statement)).scalar_one()

    async def update(self, entity: ModelExecution, **values: Any) -> NoReturn:
        raise TypeError("Model executions are append-only")

    async def delete(self, entity: ModelExecution, *, hard: bool = False) -> NoReturn:
        raise TypeError("Model executions are append-only")
