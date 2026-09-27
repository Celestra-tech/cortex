"""evidence graph: decisions linked to the evidence that produced them

Revision ID: f3c9b2d71a45
Revises: e8a4c6b1f203
Create Date: 2026-09-27 13:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c9b2d71a45"
down_revision: str | Sequence[str] | None = "e8a4c6b1f203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NODE_TYPES = (
    "decision",
    "memory",
    "message",
    "conversation",
    "document",
    "chunk",
    "knowledge",
    "benchmark",
)
EDGE_TYPES = (
    "supports",
    "references",
    "derived_from",
    "retrieved_from",
    "generated_by",
    "contradicts",
)


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "evidence_nodes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _one_of("type", NODE_TYPES), name=op.f("ck_evidence_nodes_evidence_node_type")
        ),
        sa.CheckConstraint(
            "type <> 'decision' OR ref_id IS NOT NULL",
            name=op.f("ck_evidence_nodes_decision_has_ref"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_evidence_nodes_confidence_range")
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0", name=op.f("ck_evidence_nodes_title_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_nodes_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_nodes")),
    )
    op.create_index(
        "ix_evidence_nodes_organization_id_type_created_at",
        "evidence_nodes",
        ["organization_id", "type", "created_at"],
    )
    op.create_index(
        "uq_evidence_nodes_organization_id_type_ref_id",
        "evidence_nodes",
        ["organization_id", "type", "ref_id"],
        unique=True,
        postgresql_where=sa.text("ref_id IS NOT NULL"),
    )

    op.create_table(
        "evidence_edges",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("from_node_id", sa.Uuid(), nullable=False),
        sa.Column("to_node_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _one_of("type", EDGE_TYPES), name=op.f("ck_evidence_edges_evidence_edge_type")
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_evidence_edges_confidence_range")
        ),
        sa.CheckConstraint(
            "length(btrim(explanation)) > 0", name=op.f("ck_evidence_edges_explanation_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(source)) > 0", name=op.f("ck_evidence_edges_source_not_blank")
        ),
        sa.CheckConstraint(
            "from_node_id <> to_node_id", name=op.f("ck_evidence_edges_no_self_loop")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_edges_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["from_node_id"],
            ["evidence_nodes.id"],
            name=op.f("fk_evidence_edges_from_node_id_evidence_nodes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id"],
            ["evidence_nodes.id"],
            name=op.f("fk_evidence_edges_to_node_id_evidence_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_edges")),
    )
    op.create_index(
        op.f("ix_evidence_edges_organization_id"), "evidence_edges", ["organization_id"]
    )
    op.create_index(op.f("ix_evidence_edges_to_node_id"), "evidence_edges", ["to_node_id"])
    op.create_index(
        "uq_evidence_edges_from_node_id_to_node_id_type",
        "evidence_edges",
        ["from_node_id", "to_node_id", "type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("evidence_edges")
    op.drop_table("evidence_nodes")
