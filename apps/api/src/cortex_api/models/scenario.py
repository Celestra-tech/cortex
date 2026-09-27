import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

if TYPE_CHECKING:
    from cortex_api.models.assumption import Assumption
    from cortex_api.models.outcome import Outcome


class ScenarioType(StrEnum):
    BEST_CASE = "best_case"
    BASE_CASE = "base_case"
    WORST_CASE = "worst_case"
    AGGRESSIVE = "aggressive"
    CONSERVATIVE = "conservative"


class Scenario(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One plausible path for acting on a decision, with its evaluation.

    A simulation evaluates one decision under several strategies at once; its
    scenarios share `simulation_id` and are ranked against each other. This is
    scenario planning, not forecasting: `score` and `confidence` summarize the
    scenario's explicit assumptions and outcomes, never a predicted future.

    `decision_id` is the decision's id as clients know it (for completions,
    the completion id). `decision_node_id` pins the Evidence Graph node the
    simulation read, so deleting the decision deletes its scenarios.
    """

    __tablename__ = "scenarios"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE")
    )
    simulation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    decision_node_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_nodes.id", ondelete="CASCADE"), index=True
    )
    decision_id: Mapped[uuid.UUID]
    type: Mapped[ScenarioType] = mapped_column(
        string_enum(ScenarioType, "scenario_type", length=16)
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(SmallInteger)
    # Criterion values, weights, and contributions behind `score`.
    scores: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=text("'{}'::jsonb"))
    # The request that produced the simulation: constraints, weights, risk tolerance.
    parameters: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )

    assumptions: Mapped[list["Assumption"]] = relationship(
        back_populates="scenario",
        order_by="Assumption.position",
        passive_deletes=True,
        lazy="raise",
    )
    outcomes: Mapped[list["Outcome"]] = relationship(
        back_populates="scenario",
        order_by="Outcome.position",
        passive_deletes=True,
        lazy="raise",
    )

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("rank >= 1", name="rank_positive"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        Index("uq_scenarios_simulation_id_type", "simulation_id", "type", unique=True),
        Index("uq_scenarios_simulation_id_rank", "simulation_id", "rank", unique=True),
        # Leading column also serves as the organization_id foreign key index.
        Index(
            "ix_scenarios_organization_id_decision_id_created_at",
            "organization_id",
            "decision_id",
            "created_at",
        ),
        Index("ix_scenarios_organization_id_created_at", "organization_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Scenario id={self.id} type={self.type} score={self.score:.2f}>"
