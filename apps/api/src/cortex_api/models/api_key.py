import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.organization import Organization


class ApiKeyRole(StrEnum):
    ADMIN = "admin"
    """Everything a member can do, plus managing the organization's API keys."""
    MEMBER = "member"
    """Tenant data and completions."""


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """An organization credential. Only a hash of the secret is ever stored.

    Soft deletion is revocation: revoked keys stay for audit history and keep
    their hash reserved. A key past `expires_at` is rejected like a revoked one.
    """

    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    prefix: Mapped[str | None] = mapped_column(String(16))
    """Leading characters of the secret, safe to display so users can tell keys apart."""
    role: Mapped[str] = mapped_column(
        String(16), default=ApiKeyRole.MEMBER.value, server_default=text("'member'")
    )
    """An `ApiKeyRole` value."""
    expires_at: Mapped[datetime | None]
    last_used_at: Mapped[datetime | None]
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    """The key this one replaced, when it was issued by rotation."""

    organization: Mapped["Organization"] = relationship(back_populates="api_keys", lazy="raise")

    __table_args__ = (CheckConstraint("role IN ('admin', 'member')", name="role"),)

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and self.expires_at <= now

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} name={self.name!r} role={self.role}>"
