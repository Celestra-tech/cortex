import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from cortex_api.database.base import Base
from cortex_api.database.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

if TYPE_CHECKING:
    from cortex_api.models.organization import Organization


class UserRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    full_name: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        string_enum(UserRole, "user_role"),
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.value,
    )

    organization: Mapped["Organization"] = relationship(back_populates="users", lazy="raise")

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        Index(
            "uq_users_organization_id_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    @validates("email")
    def _normalize_email(self, _key: str, value: str) -> str:
        return value.strip().lower()

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
