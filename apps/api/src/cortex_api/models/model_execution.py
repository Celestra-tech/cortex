import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from cortex_api.database.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum


class RoutingMode(StrEnum):
    AUTO = "auto"
    PREFERRED = "preferred"
    STRICT = "strict"


class ModelExecution(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One provider call. Append-only.

    A completion that falls back produces several rows sharing `completion_id`;
    exactly one of them (the last) has `success = true` unless all failed.
    """

    __tablename__ = "model_executions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    completion_id: Mapped[uuid.UUID] = mapped_column(index=True)
    attempt: Mapped[int] = mapped_column(SmallInteger)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    routing_mode: Mapped[RoutingMode] = mapped_column(string_enum(RoutingMode, "routing_mode"))
    is_fallback: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    latency_ms: Mapped[int] = mapped_column(Integer)
    prompt_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_estimate: Mapped[Decimal] = mapped_column(Numeric(14, 8), server_default=text("0"))
    success: Mapped[bool] = mapped_column(Boolean)
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    error_type: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0", name="tokens_non_negative"
        ),
        CheckConstraint("cost_estimate >= 0", name="cost_non_negative"),
        CheckConstraint(
            "(success AND error IS NULL) OR (NOT success AND error IS NOT NULL)",
            name="error_iff_failed",
        ),
        # Leading organization_id also serves the foreign key.
        Index("ix_model_executions_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "ix_model_executions_organization_id_provider_created_at",
            "organization_id",
            "provider",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ModelExecution id={self.id} {self.provider}/{self.model} "
            f"attempt={self.attempt} success={self.success}>"
        )
