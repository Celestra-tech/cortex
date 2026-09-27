import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.document_chunk import DocumentChunk


class Document(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An ingested source: its normalized text plus pipeline statistics.

    Documents are immutable once ingested; re-ingesting changed content creates
    a new document. Deletion is hard and cascades to chunks and embeddings, so
    removed knowledge can never resurface in retrieval.
    """

    __tablename__ = "documents"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(512))
    source: Mapped[str | None] = mapped_column(String(2048))
    mime_type: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    """SHA-256 of the normalized text; unique per organization."""
    content: Mapped[str] = mapped_column(Text, deferred=True)
    """Normalized text, kept so documents can be re-chunked or re-embedded."""
    byte_size: Mapped[int] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer)
    token_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    embedding_space: Mapped[str | None] = mapped_column(String(192))
    ingestion_ms: Mapped[int] = mapped_column(Integer)
    embedding_ms: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    ingestion_stats: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document",
        order_by="DocumentChunk.chunk_index",
        passive_deletes=True,
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("length(title) > 0", name="title_not_empty"),
        CheckConstraint(
            "byte_size >= 0 AND char_count >= 0 AND token_count >= 0 AND chunk_count >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint("ingestion_ms >= 0 AND embedding_ms >= 0", name="timings_non_negative"),
        # Leading organization_id also serves the foreign key.
        Index("ix_documents_organization_id_created_at", "organization_id", "created_at"),
        Index(
            "uq_documents_organization_id_content_hash",
            "organization_id",
            "content_hash",
            unique=True,
        ),
        Index(
            "ix_documents_metadata",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} title={self.title!r} chunks={self.chunk_count}>"
