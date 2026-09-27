import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Computed, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from cortex_api.database.base import Base
from cortex_api.database.mixins import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

SEARCH_CONFIG = "english"


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    """Something that happened: an event, decision, or outcome."""
    SEMANTIC = "semantic"
    """A durable fact about the world, the organization, or a user."""
    PROCEDURAL = "procedural"
    """How something is done: steps, conventions, playbooks."""
    PREFERENCE = "preference"
    """A stated like, dislike, or instruction about style."""


class Memory(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Persistent, organization-scoped knowledge distilled from conversations.

    Soft deletion is "forgetting": the row is excluded from retrieval.
    """

    __tablename__ = "memories"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    type: Mapped[MemoryType] = mapped_column(string_enum(MemoryType, "memory_type"))
    summary: Mapped[str] = mapped_column(String(1000))
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.5, server_default=text("0.5"))
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), index=True
    )
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(f"to_tsvector('{SEARCH_CONFIG}', summary || ' ' || content)", persisted=True),
        deferred=True,
    )

    __table_args__ = (
        CheckConstraint("importance >= 0 AND importance <= 1", name="importance_range"),
        CheckConstraint("length(content) > 0", name="content_not_empty"),
        # Leading column also serves as the organization_id foreign key index.
        Index("ix_memories_organization_id_created_at", "organization_id", "created_at"),
        Index("ix_memories_search_vector", "search_vector", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return f"<Memory id={self.id} type={self.type}>"
