import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.message import Message


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A thread of messages. `updated_at` doubles as the last-activity timestamp."""

    __tablename__ = "conversations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(255))

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", lazy="raise", passive_deletes=True
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id}>"
