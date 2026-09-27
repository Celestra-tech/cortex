import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cortex_api.database.base import Base
from cortex_api.database.mixins import CreatedAtMixin, UUIDPrimaryKeyMixin
from cortex_api.database.types import string_enum

if TYPE_CHECKING:
    from cortex_api.models.scenario import Scenario


class OutcomeKind(StrEnum):
    OBJECTIVE_MET = "objective_met"
    OBJECTIVE_MISSED = "objective_missed"
    CONTRADICTION = "contradiction"
    """A contradicting piece of evidence proves right."""
    CONSTRAINT_BREACH = "constraint_breach"


class Outcome(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """A consequence a scenario could lead to.

    `impact` is signed, from -1 (severe harm to the objective) to 1 (the
    objective fully achieved); `likelihood` is 0-1 under the scenario's
    assumptions. `assumption_id` names the assumption whose failure or
    success drives the outcome, when there is one.
    """

    __tablename__ = "outcomes"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[OutcomeKind] = mapped_column(string_enum(OutcomeKind, "outcome_kind", length=24))
    result: Mapped[str] = mapped_column(Text)
    impact: Mapped[float] = mapped_column(Float)
    likelihood: Mapped[float] = mapped_column(Float)
    assumption_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assumptions.id", ondelete="SET NULL"), index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)

    scenario: Mapped["Scenario"] = relationship(back_populates="outcomes", lazy="raise")

    __table_args__ = (
        CheckConstraint("impact >= -1 AND impact <= 1", name="impact_range"),
        CheckConstraint("likelihood >= 0 AND likelihood <= 1", name="likelihood_range"),
        CheckConstraint("length(btrim(result)) > 0", name="result_not_blank"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<Outcome id={self.id} impact={self.impact:+.2f} likelihood={self.likelihood:.2f}>"
