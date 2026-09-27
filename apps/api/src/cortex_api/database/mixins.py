import uuid
from datetime import datetime

from sqlalchemy import func, text
from sqlalchemy.orm import Mapped, mapped_column

from cortex_api.database.ids import uuid7


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
