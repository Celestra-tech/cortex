import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from cortex_api.models.document_chunk import DocumentChunk


class Embedding(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A chunk's vector exactly as a provider returned it (native width, unpadded).

    The ledger of every embedding space a chunk has been embedded in. It lets
    the active space be switched, or the index rebuilt, without calling
    providers again. Search reads `document_chunks.embedding`, not this table.
    """

    __tablename__ = "embeddings"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(SmallInteger)
    vector: Mapped[list[float]] = mapped_column(VECTOR(), deferred=True)

    chunk: Mapped["DocumentChunk"] = relationship(back_populates="embeddings", lazy="raise")

    __table_args__ = (
        CheckConstraint("dimensions > 0", name="dimensions_positive"),
        CheckConstraint("vector_dims(vector) = dimensions", name="vector_matches_dimensions"),
        # Leading chunk_id also serves the foreign key.
        Index(
            "uq_embeddings_chunk_id_space",
            "chunk_id",
            "provider",
            "model",
            "dimensions",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<Embedding id={self.id} {self.provider}/{self.model}@{self.dimensions}>"
