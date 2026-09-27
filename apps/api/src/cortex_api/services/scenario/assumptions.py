"""What a scenario takes to be true.

Every input the simulator uses becomes an explicit assumption with a
confidence and a source: supporting evidence that must keep holding,
contradictions that must not materialize, constraints that must be respected,
assumptions stated in the request, and the scenario's own stipulations. The
`baseline` is the confidence before a scenario's stance is applied, so a
reader can see exactly how far each scenario departs from the record.
"""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from cortex_api.models.assumption import AssumptionKind
from cortex_api.models.evidence_node import EvidenceNodeType
from cortex_api.services.evidence.provenance import clamp, excerpt

SOURCE_EVIDENCE = "cortex.evidence"
SOURCE_PLANNER = "cortex.scenario.planner"

MAX_TITLE = 160

EVIDENCE_LABELS: dict[EvidenceNodeType, str] = {
    EvidenceNodeType.DECISION: "Prior decision",
    EvidenceNodeType.MEMORY: "Memory",
    EvidenceNodeType.MESSAGE: "Message",
    EvidenceNodeType.CONVERSATION: "Conversation",
    EvidenceNodeType.DOCUMENT: "Document",
    EvidenceNodeType.CHUNK: "Source passage",
    EvidenceNodeType.KNOWLEDGE: "Retrieval",
    EvidenceNodeType.BENCHMARK: "Model run",
}


class Severity(StrEnum):
    HARD = "hard"
    """Breaching it defeats the decision."""
    SOFT = "soft"
    """Breaching it is costly but survivable."""


@dataclass(frozen=True)
class EvidenceItem:
    """One piece of supporting evidence, as ranked by the Evidence Graph."""

    node_id: uuid.UUID
    type: EvidenceNodeType
    title: str
    strength: float
    depth: int

    @property
    def label(self) -> str:
        return f"{EVIDENCE_LABELS[self.type]} “{excerpt(self.title, MAX_TITLE)}”"


@dataclass(frozen=True)
class ContradictionItem:
    """Evidence that argues against the decision; `strength` is edge x node confidence."""

    node_id: uuid.UUID
    type: EvidenceNodeType
    title: str
    strength: float
    explanation: str

    @property
    def label(self) -> str:
        return f"{EVIDENCE_LABELS[self.type]} “{excerpt(self.title, MAX_TITLE)}”"


@dataclass(frozen=True)
class Constraint:
    statement: str
    severity: Severity = Severity.SOFT


@dataclass(frozen=True)
class StatedAssumption:
    statement: str
    confidence: float


@dataclass(frozen=True)
class AssumptionDraft:
    """An assumption before it is stored. `key` links outcomes to it within a plan."""

    key: str
    kind: AssumptionKind
    statement: str
    baseline: float
    confidence: float
    source: str
    evidence_node_id: uuid.UUID | None = None

    @property
    def shift(self) -> float:
        return self.confidence - self.baseline


def shift(confidence: float, optimism: float) -> float:
    """Moves a confidence toward 1 (optimism > 0) or toward 0 (optimism < 0).

    Proportional in both directions, so certainties stay certain and nothing
    leaves [0, 1]: +0.5 closes half the gap to 1, -0.5 halves the confidence.
    """
    confidence = clamp(confidence)
    optimism = max(-1.0, min(1.0, optimism))
    if optimism >= 0:
        return confidence + optimism * (1.0 - confidence)
    return confidence * (1.0 + optimism)


def for_evidence(item: EvidenceItem, optimism: float) -> AssumptionDraft:
    return AssumptionDraft(
        key=f"evidence:{item.node_id}",
        kind=AssumptionKind.EVIDENCE,
        statement=f"{item.label} remains accurate and applicable.",
        baseline=clamp(item.strength),
        confidence=shift(item.strength, optimism),
        source=SOURCE_EVIDENCE,
        evidence_node_id=item.node_id,
    )


def realization(item: ContradictionItem, factor: float) -> float:
    """How likely the contradiction proves right under a scenario."""
    return clamp(item.strength * factor)


def for_contradiction(item: ContradictionItem, factor: float) -> AssumptionDraft:
    return AssumptionDraft(
        key=f"contradiction:{item.node_id}",
        kind=AssumptionKind.CONTRADICTION,
        statement=f"The objection raised by {item.label} does not materialize.",
        baseline=1.0 - clamp(item.strength),
        confidence=1.0 - realization(item, factor),
        source=SOURCE_EVIDENCE,
        evidence_node_id=item.node_id,
    )


def for_constraint(
    index: int, constraint: Constraint, adherence: float, baseline: float, source: str
) -> AssumptionDraft:
    return AssumptionDraft(
        key=f"constraint:{index}",
        kind=AssumptionKind.CONSTRAINT,
        statement=f"{constraint.severity.capitalize()} constraint respected:"
        f" {constraint.statement}",
        baseline=clamp(baseline),
        confidence=clamp(adherence),
        source=source,
    )


def for_stated(
    index: int, stated: StatedAssumption, optimism: float, source: str
) -> AssumptionDraft:
    return AssumptionDraft(
        key=f"stated:{index}",
        kind=AssumptionKind.STATED,
        statement=stated.statement,
        baseline=clamp(stated.confidence),
        confidence=shift(stated.confidence, optimism),
        source=source,
    )


def stipulation(index: int, statement: str) -> AssumptionDraft:
    """A condition the scenario defines rather than estimates, so it is certain by construction."""
    return AssumptionDraft(
        key=f"strategy:{index}",
        kind=AssumptionKind.STRATEGY,
        statement=statement,
        baseline=1.0,
        confidence=1.0,
        source=SOURCE_PLANNER,
    )
