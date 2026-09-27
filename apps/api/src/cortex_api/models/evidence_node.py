import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum


class EvidenceNodeType(StrEnum):
    DECISION = "decision"
    MEMORY = "memory"
    MESSAGE = "message"
    CONVERSATION = "conversation"
    DOCUMENT = "document"
    CHUNK = "chunk"
    KNOWLEDGE = "knowledge"
    BENCHMARK = "benchmark"


class EvidenceNode(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One thing that produced, or was produced by, a decision.

    `ref_id` is the id of the record the node stands for: a memory, message,
    conversation, document, chunk, knowledge query, or model execution. A
    record appears once per organization, so evidence shared by many
    decisions is one node with many edges.

    Nodes are snapshots. Title, confidence, and metadata are captured when the
    node is first recorded, and there is deliberately no foreign key to the
    referenced record: the evidence behind a decision must outlive later
    edits and deletions of its sources.

    Decision nodes always carry a `ref_id`; for completions it is the
    completion id, so a decision is addressed by the id clients already hold.
    """

    __tablename__ = "evidence_nodes"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    type: Mapped[EvidenceNodeType] = mapped_column(
        string_enum(EvidenceNodeType, "evidence_node_type", length=16)
    )
    ref_id: Mapped[uuid.UUID | None]
    title: Mapped[str] = mapped_column(String(500))
    confidence: Mapped[float] = mapped_column(Float)
    # `metadata` is reserved on declarative classes, hence the trailing underscore.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", default=dict, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("type <> 'decision' OR ref_id IS NOT NULL", name="decision_has_ref"),
        Index(
            "uq_evidence_nodes_organization_id_type_ref_id",
            "organization_id",
            "type",
            "ref_id",
            unique=True,
            postgresql_where=text("ref_id IS NOT NULL"),
        ),
        # Leading column also serves as the organization_id foreign key index.
        Index(
            "ix_evidence_nodes_organization_id_type_created_at",
            "organization_id",
            "type",
            "created_at",
        ),
    )

    def __repr__(self) -> str:
        return f"<EvidenceNode id={self.id} type={self.type} title={self.title!r}>"
