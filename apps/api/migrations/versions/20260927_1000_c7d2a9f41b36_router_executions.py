"""router: model_executions, organizations.settings

Revision ID: c7d2a9f41b36
Revises: 8b41d6e2c5a7
Create Date: 2026-09-27 10:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d2a9f41b36"
down_revision: str | Sequence[str] | None = "8b41d6e2c5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb(name: str) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column("organizations", _jsonb("settings"))

    op.create_table(
        "model_executions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("completion_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("routing_mode", sa.String(length=32), nullable=False),
        sa.Column("is_fallback", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "cost_estimate",
            sa.Numeric(precision=14, scale=8),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column("error_type", sa.String(length=32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _jsonb("metadata"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("attempt >= 1", name=op.f("ck_model_executions_attempt_positive")),
        sa.CheckConstraint(
            "latency_ms >= 0", name=op.f("ck_model_executions_latency_non_negative")
        ),
        sa.CheckConstraint(
            "prompt_tokens >= 0 AND completion_tokens >= 0",
            name=op.f("ck_model_executions_tokens_non_negative"),
        ),
        sa.CheckConstraint(
            "cost_estimate >= 0", name=op.f("ck_model_executions_cost_non_negative")
        ),
        sa.CheckConstraint(
            "(success AND error IS NULL) OR (NOT success AND error IS NOT NULL)",
            name=op.f("ck_model_executions_error_iff_failed"),
        ),
        sa.CheckConstraint(
            "routing_mode IN ('auto', 'preferred', 'strict')",
            name=op.f("ck_model_executions_routing_mode"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_model_executions_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_executions")),
    )
    op.create_index(
        op.f("ix_model_executions_completion_id"), "model_executions", ["completion_id"]
    )
    op.create_index(
        "ix_model_executions_organization_id_created_at",
        "model_executions",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_model_executions_organization_id_provider_created_at",
        "model_executions",
        ["organization_id", "provider", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_executions_organization_id_provider_created_at", table_name="model_executions"
    )
    op.drop_index("ix_model_executions_organization_id_created_at", table_name="model_executions")
    op.drop_index(op.f("ix_model_executions_completion_id"), table_name="model_executions")
    op.drop_table("model_executions")
    op.drop_column("organizations", "settings")
