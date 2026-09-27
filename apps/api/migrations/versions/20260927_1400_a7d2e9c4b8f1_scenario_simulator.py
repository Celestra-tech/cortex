"""scenario simulator: scenarios, their assumptions, and outcomes

Revision ID: a7d2e9c4b8f1
Revises: f3c9b2d71a45
Create Date: 2026-09-27 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7d2e9c4b8f1"
down_revision: str | Sequence[str] | None = "f3c9b2d71a45"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCENARIO_TYPES = ("best_case", "base_case", "worst_case", "aggressive", "conservative")
ASSUMPTION_KINDS = ("evidence", "contradiction", "constraint", "strategy", "stated")
OUTCOME_KINDS = ("objective_met", "objective_missed", "contradiction", "constraint_breach")


def _one_of(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _id() -> sa.Column[sa.Uuid]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _created_at() -> sa.Column[sa.DateTime]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def _jsonb(name: str) -> sa.Column[postgresql.JSONB]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'{}'::jsonb"),
        nullable=False,
    )


def _organization_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organizations.id"],
        name=op.f(f"fk_{table}_organization_id_organizations"),
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "scenarios",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("simulation_id", sa.Uuid(), nullable=False),
        sa.Column("decision_node_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        _jsonb("scores"),
        _jsonb("parameters"),
        _created_at(),
        sa.CheckConstraint(
            _one_of("type", SCENARIO_TYPES), name=op.f("ck_scenarios_scenario_type")
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name=op.f("ck_scenarios_score_range")),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_scenarios_confidence_range")
        ),
        sa.CheckConstraint("rank >= 1", name=op.f("ck_scenarios_rank_positive")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_scenarios_name_not_blank")),
        _organization_fk("scenarios"),
        sa.ForeignKeyConstraint(
            ["decision_node_id"],
            ["evidence_nodes.id"],
            name=op.f("fk_scenarios_decision_node_id_evidence_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scenarios")),
    )
    op.create_index(op.f("ix_scenarios_simulation_id"), "scenarios", ["simulation_id"])
    op.create_index(op.f("ix_scenarios_decision_node_id"), "scenarios", ["decision_node_id"])
    op.create_index(
        "uq_scenarios_simulation_id_type", "scenarios", ["simulation_id", "type"], unique=True
    )
    op.create_index(
        "uq_scenarios_simulation_id_rank", "scenarios", ["simulation_id", "rank"], unique=True
    )
    op.create_index(
        "ix_scenarios_organization_id_decision_id_created_at",
        "scenarios",
        ["organization_id", "decision_id", "created_at"],
    )
    op.create_index(
        "ix_scenarios_organization_id_created_at", "scenarios", ["organization_id", "created_at"]
    )

    op.create_table(
        "assumptions",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("baseline_confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("evidence_node_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            _one_of("kind", ASSUMPTION_KINDS), name=op.f("ck_assumptions_assumption_kind")
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name=op.f("ck_assumptions_confidence_range")
        ),
        sa.CheckConstraint(
            "baseline_confidence >= 0 AND baseline_confidence <= 1",
            name=op.f("ck_assumptions_baseline_confidence_range"),
        ),
        sa.CheckConstraint(
            "length(btrim(statement)) > 0", name=op.f("ck_assumptions_statement_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(source)) > 0", name=op.f("ck_assumptions_source_not_blank")
        ),
        sa.CheckConstraint("position >= 0", name=op.f("ck_assumptions_position_non_negative")),
        _organization_fk("assumptions"),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_assumptions_scenario_id_scenarios"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_node_id"],
            ["evidence_nodes.id"],
            name=op.f("fk_assumptions_evidence_node_id_evidence_nodes"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assumptions")),
    )
    op.create_index(op.f("ix_assumptions_organization_id"), "assumptions", ["organization_id"])
    op.create_index(op.f("ix_assumptions_scenario_id"), "assumptions", ["scenario_id"])
    op.create_index(op.f("ix_assumptions_evidence_node_id"), "assumptions", ["evidence_node_id"])

    op.create_table(
        "outcomes",
        _id(),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("impact", sa.Float(), nullable=False),
        sa.Column("likelihood", sa.Float(), nullable=False),
        sa.Column("assumption_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        _created_at(),
        sa.CheckConstraint(_one_of("kind", OUTCOME_KINDS), name=op.f("ck_outcomes_outcome_kind")),
        sa.CheckConstraint("impact >= -1 AND impact <= 1", name=op.f("ck_outcomes_impact_range")),
        sa.CheckConstraint(
            "likelihood >= 0 AND likelihood <= 1", name=op.f("ck_outcomes_likelihood_range")
        ),
        sa.CheckConstraint("length(btrim(result)) > 0", name=op.f("ck_outcomes_result_not_blank")),
        sa.CheckConstraint("position >= 0", name=op.f("ck_outcomes_position_non_negative")),
        _organization_fk("outcomes"),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
            name=op.f("fk_outcomes_scenario_id_scenarios"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assumption_id"],
            ["assumptions.id"],
            name=op.f("fk_outcomes_assumption_id_assumptions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outcomes")),
    )
    op.create_index(op.f("ix_outcomes_organization_id"), "outcomes", ["organization_id"])
    op.create_index(op.f("ix_outcomes_scenario_id"), "outcomes", ["scenario_id"])
    op.create_index(op.f("ix_outcomes_assumption_id"), "outcomes", ["assumption_id"])


def downgrade() -> None:
    op.drop_table("outcomes")
    op.drop_table("assumptions")
    op.drop_table("scenarios")
