"""Combines the five criteria into one normalized score and ranks scenarios.

Each criterion is turned into a desirability in [0, 1] (uncertainty and risk
exposure are inverted, since less is better) and weighted. Weights are
normalized to sum to 1, so the score is also in [0, 1] and every criterion's
contribution is visible. Risk tolerance trades objective alignment against
risk exposure: at 0.5 the weights are unchanged; toward 1 alignment counts up
to 1.5x and risk down to 0.5x, and the reverse toward 0.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from cortex_api.services.evidence.provenance import clamp
from cortex_api.services.scenario.evaluator import Evaluation


class Criterion(StrEnum):
    EVIDENCE_QUALITY = "evidence_quality"
    UNCERTAINTY = "uncertainty"
    CONSTRAINT_SATISFACTION = "constraint_satisfaction"
    OBJECTIVE_ALIGNMENT = "objective_alignment"
    RISK_EXPOSURE = "risk_exposure"


LOWER_IS_BETTER = frozenset({Criterion.UNCERTAINTY, Criterion.RISK_EXPOSURE})

DEFAULT_WEIGHTS: dict[Criterion, float] = {
    Criterion.EVIDENCE_QUALITY: 0.2,
    Criterion.UNCERTAINTY: 0.15,
    Criterion.CONSTRAINT_SATISFACTION: 0.2,
    Criterion.OBJECTIVE_ALIGNMENT: 0.25,
    Criterion.RISK_EXPOSURE: 0.2,
}
DEFAULT_RISK_TOLERANCE = 0.5


class WeightError(ValueError):
    pass


@dataclass(frozen=True)
class CriterionScore:
    value: float
    desirability: float
    weight: float

    @property
    def contribution(self) -> float:
        return self.desirability * self.weight

    def as_dict(self) -> dict[str, float]:
        return {
            "value": self.value,
            "desirability": self.desirability,
            "weight": self.weight,
            "contribution": self.contribution,
        }


@dataclass(frozen=True)
class ScoreCard:
    score: float
    confidence: float
    criteria: dict[Criterion, CriterionScore]


def weights_for(
    overrides: Mapping[Criterion, float] | None = None,
    risk_tolerance: float = DEFAULT_RISK_TOLERANCE,
) -> dict[Criterion, float]:
    """Criterion weights after overrides and risk tolerance, summing to 1."""
    weights = dict(DEFAULT_WEIGHTS)
    for criterion, weight in (overrides or {}).items():
        if weight < 0:
            raise WeightError(f"weight for {criterion} must not be negative")
        weights[criterion] = weight
    tolerance = clamp(risk_tolerance)
    weights[Criterion.OBJECTIVE_ALIGNMENT] *= 0.5 + tolerance
    weights[Criterion.RISK_EXPOSURE] *= 1.5 - tolerance
    total = sum(weights.values())
    if total <= 0:
        raise WeightError("at least one criterion needs a positive weight")
    return {criterion: weight / total for criterion, weight in weights.items()}


def desirability(criterion: Criterion, value: float) -> float:
    value = clamp(value)
    return 1.0 - value if criterion in LOWER_IS_BETTER else value


def confidence(evaluation: Evaluation) -> float:
    """How far to trust the scenario itself: certain assumptions resting on good evidence.

    Certainty counts in full; evidence quality scales it between one half
    (no evidence) and all of it (strong, diverse evidence).
    """
    return clamp((1.0 - evaluation.uncertainty) * (0.5 + 0.5 * evaluation.evidence_quality))


def score(evaluation: Evaluation, weights: Mapping[Criterion, float]) -> ScoreCard:
    values = evaluation.as_dict()
    criteria = {
        criterion: CriterionScore(
            value=values[criterion],
            desirability=desirability(criterion, values[criterion]),
            weight=weights[criterion],
        )
        for criterion in Criterion
    }
    return ScoreCard(
        score=clamp(sum(c.contribution for c in criteria.values())),
        confidence=confidence(evaluation),
        criteria=criteria,
    )


def rank(cards: Sequence[ScoreCard]) -> list[int]:
    """1-based ranks, best first. Ties go to the more confident scenario, then input order."""
    order = sorted(range(len(cards)), key=lambda i: (-cards[i].score, -cards[i].confidence, i))
    ranks = [0] * len(cards)
    for position, index in enumerate(order, start=1):
        ranks[index] = position
    return ranks
