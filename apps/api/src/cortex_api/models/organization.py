from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.api_key import ApiKey
    from cortex_api.models.user import User

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(63))
    settings: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    """Per-tenant configuration. `settings["routing"]` holds the routing policy."""

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", lazy="raise", passive_deletes=True
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="organization", lazy="raise", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        # Slugs are reusable once an organization is soft-deleted.
        Index(
            "uq_organizations_slug",
            "slug",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id} slug={self.slug!r}>"
