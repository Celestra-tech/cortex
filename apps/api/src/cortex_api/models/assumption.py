import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

if TYPE_CHECKING:
    from cortex_api.models.scenario import Scenario


class AssumptionKind(StrEnum):
    EVIDENCE = "evidence"
    """A piece of supporting evidence continues to hold."""
    CONTRADICTION = "contradiction"
    """A contradicting piece of evidence does not materialize."""
    CONSTRAINT = "constraint"
    """A stated constraint is respected."""
    STRATEGY = "strategy"
    """A stipulation of the scenario itself, such as its commitment level."""
    STATED = "stated"
    """An assumption supplied with the simulation request."""


class Assumption(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Something a scenario takes to be true, and how confidently.

    `baseline_confidence` is the confidence before the scenario's stance was
    applied (the evidence as recorded); `confidence` is what the scenario
    assumes. Evidence and contradiction assumptions point at the Evidence
    Graph node they rest on.
    """

    __tablename__ = "assumptions"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[AssumptionKind] = mapped_column(
        string_enum(AssumptionKind, "assumption_kind", length=16)
    )
    statement: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    baseline_confidence: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(255))
    evidence_node_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evidence_nodes.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)

    scenario: Mapped["Scenario"] = relationship(back_populates="assumptions", lazy="raise")

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint(
            "baseline_confidence >= 0 AND baseline_confidence <= 1",
            name="baseline_confidence_range",
        ),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_not_blank"),
        CheckConstraint("length(btrim(source)) > 0", name="source_not_blank"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Assumption id={self.id} kind={self.kind} confidence={self.confidence:.2f}>"
