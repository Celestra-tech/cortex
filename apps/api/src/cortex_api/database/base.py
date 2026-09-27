import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from cortex_api.database.ids import uuid7

# Deterministic constraint names keep Alembic autogenerate diffs stable across environments.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # SQLAlchemy types this as an instance attribute, so it cannot be a ClassVar.
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }

    # Server-generated values (timestamps) come back via RETURNING, so async code
    # never triggers an implicit lazy load when reading them after a flush.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid7,
        server_default=text("gen_random_uuid()"),
        sort_order=-100,
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)


class TimestampMixin(CreatedAtMixin):
    # The migration also installs a BEFORE UPDATE trigger so raw SQL updates stay accurate.
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(default=None, sort_order=102)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
