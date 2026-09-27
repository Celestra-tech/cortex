import uuid
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.models.memory import SEARCH_CONFIG

if TYPE_CHECKING:
    from cortex_api.models.document import Document
    from cortex_api.models.embedding import Embedding

EMBEDDING_DIMENSIONS = 1536
"""Width of the indexed `embedding` column.

Providers with fewer native dimensions are zero-padded, which preserves cosine
similarity exactly. Widening requires a migration and a re-embed.
"""


class DocumentChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A retrievable span of a document.

    `embedding` is the searchable copy of the chunk's vector in the active
    embedding space. Vectors from different spaces are not comparable, so
    vector search always filters on `embedding_space`; chunks from another space
    remain reachable through keyword search until they are re-embedded.
    """

    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    # Denormalized so tenant-scoped search never needs a join to filter.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(1024))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(
        VECTOR(EMBEDDING_DIMENSIONS), deferred=True
    )
    embedding_space: Mapped[str | None] = mapped_column(String(192))
    search_vector: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            f"setweight(to_tsvector('{SEARCH_CONFIG}', coalesce(section, '')), 'A') || "
            f"setweight(to_tsvector('{SEARCH_CONFIG}', content), 'B')",
            persisted=True,
        ),
        deferred=True,
    )

    document: Mapped["Document"] = relationship(back_populates="chunks", lazy="raise")
    embeddings: Mapped[list["Embedding"]] = relationship(
        back_populates="chunk", passive_deletes=True, lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
        CheckConstraint("length(content) > 0", name="content_not_empty"),
        CheckConstraint("token_count >= 0", name="token_count_non_negative"),
        CheckConstraint("char_start >= 0 AND char_end > char_start", name="char_span_valid"),
        CheckConstraint(
            "page_start IS NULL OR (page_start >= 1 AND page_end >= page_start)",
            name="page_span_valid",
        ),
        CheckConstraint(
            "(embedding IS NULL) = (embedding_space IS NULL)", name="embedding_space_iff_embedding"
        ),
        # Leading document_id also serves the foreign key.
        Index(
            "uq_document_chunks_document_id_chunk_index", "document_id", "chunk_index", unique=True
        ),
        Index(
            "ix_document_chunks_organization_id_embedding_space",
            "organization_id",
            "embedding_space",
        ),
        Index("ix_document_chunks_search_vector", "search_vector", postgresql_using="gin"),
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<DocumentChunk id={self.id} document={self.document_id} #{self.chunk_index}>"
