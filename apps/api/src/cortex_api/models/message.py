import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

if TYPE_CHECKING:
    from cortex_api.models.conversation import Conversation


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Immutable conversation turn. Postgres is the source of truth; Redis holds a hot copy."""

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(string_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text)
    # Estimated once at write time so context windows never re-encode history.
    token_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages", lazy="raise")

    __table_args__ = (
        CheckConstraint("length(content) > 0", name="content_not_empty"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        # Leading column also serves as the conversation_id foreign key index.
        Index("ix_messages_conversation_id_created_at", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role}>"
