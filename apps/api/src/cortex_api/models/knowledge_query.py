import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum


class SearchMode(StrEnum):
    HYBRID = "hybrid"
    VECTOR = "vector"
    KEYWORD = "keyword"


class KnowledgeQuery(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One retrieval: what was asked, what came back, how fast, and how good it looked.

    When the retrieval grounds a completion, `completion_id` links it to the
    model executions and `cited` records which citations the answer used.
    """

    __tablename__ = "knowledge_queries"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    query: Mapped[str] = mapped_column(Text)
    mode: Mapped[SearchMode] = mapped_column(string_enum(SearchMode, "search_mode", length=16))
    top_k: Mapped[int] = mapped_column(SmallInteger)
    filters: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    embedding_space: Mapped[str | None] = mapped_column(String(192))
    result_count: Mapped[int] = mapped_column(Integer)
    vector_candidates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    keyword_candidates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    citation_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    context_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    latency_ms: Mapped[int] = mapped_column(Integer)
    embedding_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    vector_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    keyword_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    query_embedding_cached: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    top_score: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, server_default=text("0"))
    coverage: Mapped[float | None] = mapped_column(Float)
    agreement: Mapped[float | None] = mapped_column(Float)
    results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    completion_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    cited: Mapped[list[int] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("top_k >= 1", name="top_k_positive"),
        CheckConstraint(
            "result_count >= 0 AND vector_candidates >= 0 AND keyword_candidates >= 0 "
            "AND citation_count >= 0 AND context_tokens >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "latency_ms >= 0 AND embedding_ms >= 0 AND vector_ms >= 0 AND keyword_ms >= 0",
            name="timings_non_negative",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        # Leading organization_id also serves the foreign key.
        Index("ix_knowledge_queries_organization_id_created_at", "organization_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<KnowledgeQuery id={self.id} mode={self.mode} results={self.result_count}>"
