"""Measures a plan on five criteria, each normalized to [0, 1].

- `evidence_quality`: how well the evidence the scenario relies on
  corroborates the decision. A noisy-OR of the recorded strengths (the
  scenario's optimism does not inflate it), scaled down when the evidence
  comes from fewer than `DIVERSITY_TARGET` kinds of source.
- `uncertainty`: the mean binary entropy of the scenario's estimated
  assumptions. 0 when every assumption is certain either way, 1 when every
  one is a coin flip. Stipulations are excluded: they are defined, not
  estimated.
- `constraint_satisfaction`: the chance each constraint is respected, with
  hard constraints weighted `HARD_WEIGHT` times soft ones. 1 without
  constraints.
- `objective_alignment`: how far the path is expected to move the objective,
  the success likelihood times the commitment.
- `risk_exposure`: the chance of at least one harmful outcome, each weighted
  by its severity: 1 - prod(1 - likelihood x |impact|).
"""

import math
from dataclasses import dataclass

from cortex_api.models.assumption import AssumptionKind
from cortex_api.services.evidence.provenance import clamp
from cortex_api.services.scenario.assumptions import Severity
from cortex_api.services.scenario.planner import ScenarioPlan, noisy_or

DIVERSITY_TARGET = 3
DIVERSITY_WEIGHT = 0.3
HARD_WEIGHT = 2.0


@dataclass(frozen=True)
class Evaluation:
    evidence_quality: float
    uncertainty: float
    constraint_satisfaction: float
    objective_alignment: float
    risk_exposure: float

    def as_dict(self) -> dict[str, float]:
        return {
            "evidence_quality": self.evidence_quality,
            "uncertainty": self.uncertainty,
            "constraint_satisfaction": self.constraint_satisfaction,
            "objective_alignment": self.objective_alignment,
            "risk_exposure": self.risk_exposure,
        }


def entropy(p: float) -> float:
    """Binary entropy in bits: 0 at certainty, 1 at a coin flip."""
    p = clamp(p)
    if p in (0.0, 1.0):
        return 0.0
    return -(p * math.log2(p) + (1.0 - p) * math.log2(1.0 - p))


def evidence_quality(plan: ScenarioPlan) -> float:
    if not plan.relied:
        return 0.0
    corroboration = noisy_or(item.strength for item in plan.relied)
    kinds = len({item.type for item in plan.relied})
    diversity = min(1.0, kinds / DIVERSITY_TARGET)
    return corroboration * (1.0 - DIVERSITY_WEIGHT + DIVERSITY_WEIGHT * diversity)


def uncertainty(plan: ScenarioPlan) -> float:
    estimated = [a for a in plan.assumptions if a.kind is not AssumptionKind.STRATEGY]
    if not estimated:
        return 1.0
    return sum(entropy(a.confidence) for a in estimated) / len(estimated)


def constraint_satisfaction(plan: ScenarioPlan) -> float:
    assumptions = plan.assumptions_of(AssumptionKind.CONSTRAINT)
    if not assumptions:
        return 1.0
    weights = [HARD_WEIGHT if c.severity is Severity.HARD else 1.0 for c in plan.constraints]
    total = sum(weights)
    return sum(w * a.confidence for w, a in zip(weights, assumptions, strict=True)) / total


def objective_alignment(plan: ScenarioPlan) -> float:
    return clamp(plan.success_likelihood * plan.strategy.commitment)


def risk_exposure(plan: ScenarioPlan) -> float:
    safe = 1.0
    for outcome in plan.outcomes:
        if outcome.impact < 0:
            safe *= 1.0 - clamp(outcome.likelihood * -outcome.impact)
    return 1.0 - safe


def evaluate(plan: ScenarioPlan) -> Evaluation:
    return Evaluation(
        evidence_quality=clamp(evidence_quality(plan)),
        uncertainty=clamp(uncertainty(plan)),
        constraint_satisfaction=clamp(constraint_satisfaction(plan)),
        objective_alignment=objective_alignment(plan),
        risk_exposure=clamp(risk_exposure(plan)),
    )
