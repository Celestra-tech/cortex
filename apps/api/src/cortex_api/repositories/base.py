import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import ColumnElement, Select, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from cortex_api.database.base import Base, SoftDeleteMixin


class NotFoundError(LookupError):
    def __init__(self, model: type[Base], identifier: object) -> None:
        super().__init__(f"{model.__name__} {identifier} not found")
        self.model = model
        self.identifier = identifier


class BaseRepository[ModelT: Base]:
    """Async data access for a single model.

    Repositories flush but never commit: the caller owns the transaction so
    several repository calls can form one unit of work.

    Soft-deletable models hide deleted rows from every read unless
    `include_deleted=True` is passed.
    """

    model: ClassVar[type[Any]]
    default_limit: ClassVar[int] = 100
    max_limit: ClassVar[int] = 1000

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def soft_deletes(self) -> bool:
        return issubclass(self.model, SoftDeleteMixin)

    @property
    def _primary_key(self) -> ColumnElement[Any]:
        column: ColumnElement[Any] = inspect(self.model).primary_key[0]
        return column

    def select(self, *, include_deleted: bool = False) -> Select[ModelT]:
        statement: Select[ModelT] = select(self.model)
        if self.soft_deletes and not include_deleted:
            deleted_at: InstrumentedAttribute[datetime | None] = self.model.deleted_at
            statement = statement.where(deleted_at.is_(None))
        return statement

    async def get(self, id_: uuid.UUID, *, include_deleted: bool = False) -> ModelT | None:
        statement = self.select(include_deleted=include_deleted).where(self._primary_key == id_)
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_raise(self, id_: uuid.UUID, *, include_deleted: bool = False) -> ModelT:
        entity = await self.get(id_, include_deleted=include_deleted)
        if entity is None:
            raise NotFoundError(self.model, id_)
        return entity

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        include_deleted: bool = False,
    ) -> Sequence[ModelT]:
        """Ordered by primary key; UUIDv7 keys make that creation order."""
        bounded = min(limit or self.default_limit, self.max_limit)
        statement = (
            self.select(include_deleted=include_deleted)
            .order_by(self._primary_key)
            .limit(bounded)
            .offset(offset)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def count(self, *, include_deleted: bool = False) -> int:
        subquery = self.select(include_deleted=include_deleted).subquery()
        result = await self.session.execute(select(func.count()).select_from(subquery))
        return result.scalar_one()

    async def create(self, **values: Any) -> ModelT:
        entity: ModelT = self.model(**values)
        return await self.add(entity)

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def update(self, entity: ModelT, **values: Any) -> ModelT:
        mapper = inspect(self.model)
        unknown = set(values) - set(mapper.column_attrs.keys())
        if unknown:
            raise AttributeError(f"{self.model.__name__} has no column(s): {sorted(unknown)}")
        for key, value in values.items():
            setattr(entity, key, value)
        await self.session.flush()
        return entity

    async def delete(self, entity: ModelT, *, hard: bool = False) -> None:
        if isinstance(entity, SoftDeleteMixin) and not hard:
            entity.deleted_at = datetime.now(UTC)
        else:
            await self.session.delete(entity)
        await self.session.flush()

    async def restore(self, entity: ModelT) -> ModelT:
        if not isinstance(entity, SoftDeleteMixin):
            raise TypeError(f"{self.model.__name__} does not support soft delete")
        entity.deleted_at = None
        await self.session.flush()
        return entity
