import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.organization import Organization
    from cortex_api.models.user import User


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Append-only record of an action. No updated_at or soft delete by design.

    Foreign keys are nullable with ON DELETE SET NULL so history survives the
    removal of the organization or actor it references.
    """

    __tablename__ = "audit_logs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(255))
    # `metadata` is reserved on declarative classes, hence the trailing underscore.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    organization: Mapped["Organization | None"] = relationship(lazy="raise")
    actor: Mapped["User | None"] = relationship(lazy="raise")

    __table_args__ = (
        # Leading column also serves as the organization_id foreign key index.
        Index("ix_audit_logs_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "ix_audit_logs_metadata",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} action={self.action!r}>"
