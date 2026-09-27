import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from cortex_api.models.assumption import Assumption, AssumptionKind
from cortex_api.models.outcome import Outcome, OutcomeKind
from cortex_api.models.scenario import Scenario, ScenarioType
from cortex_api.repositories.scenario_repository import SimulationSummary
from cortex_api.services.evidence.graph import DEFAULT_DEPTH, MAX_DEPTH
from cortex_api.services.scenario.assumptions import Severity
from cortex_api.services.scenario.scoring import (
    DEFAULT_RISK_TOLERANCE,
    DEFAULT_WEIGHTS,
    Criterion,
)

Unit = Annotated[float, Field(ge=0.0, le=1.0)]
Statement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Objective = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ConstraintInput(BaseModel):
    statement: Statement
    severity: Severity = Field(
        default=Severity.SOFT,
        description="`hard` constraints must hold for the decision to stand; `soft` ones are "
        "costly to breach but survivable.",
    )


class StatedAssumptionInput(BaseModel):
    statement: Statement
    confidence: Unit = Field(description="How likely the assumption holds, as you see it today.")


class ScenarioWeights(BaseModel):
    """Relative importance of each criterion; omitted ones keep their defaults."""

    evidence_quality: float | None = Field(default=None, ge=0.0)
    uncertainty: float | None = Field(default=None, ge=0.0)
    constraint_satisfaction: float | None = Field(default=None, ge=0.0)
    objective_alignment: float | None = Field(default=None, ge=0.0)
    risk_exposure: float | None = Field(default=None, ge=0.0)

    def overrides(self) -> dict[Criterion, float]:
        return {
            Criterion(name): value for name, value in self.model_dump().items() if value is not None
        }


class SimulationCreate(BaseModel):
    decision_id: uuid.UUID = Field(
        description="The decision to plan for: for completions, the completion id."
    )
    objective: Objective | None = Field(
        default=None,
        description="What acting on the decision should achieve. Defaults to the decision title.",
    )
    constraints: list[ConstraintInput] = Field(default_factory=list, max_length=20)
    assumptions: list[StatedAssumptionInput] = Field(
        default_factory=list,
        max_length=20,
        description="Conditions the objective depends on beyond the recorded evidence.",
    )
    risk_tolerance: Unit = Field(
        default=DEFAULT_RISK_TOLERANCE,
        description="0 favors avoiding harm, 1 favors reaching the objective.",
    )
    weights: ScenarioWeights | None = None
    types: list[ScenarioType] | None = Field(
        default=None,
        min_length=1,
        description="Scenarios to generate; all five by default.",
    )
    depth: int = Field(
        default=DEFAULT_DEPTH, ge=1, le=MAX_DEPTH, description="Evidence hops to consider."
    )

    @field_validator("types")
    @classmethod
    def _unique_types(cls, value: list[ScenarioType] | None) -> list[ScenarioType] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("types must not repeat")
        return value

    @model_validator(mode="after")
    def _some_weight(self) -> "SimulationCreate":
        if self.weights is not None:
            merged = {**DEFAULT_WEIGHTS, **self.weights.overrides()}
            if sum(merged.values()) <= 0:
                raise ValueError("at least one criterion needs a positive weight")
        return self


class AssumptionRead(BaseModel):
    id: uuid.UUID
    kind: AssumptionKind
    statement: str
    confidence: float = Field(description="What the scenario assumes.")
    baseline_confidence: float = Field(description="The same assumption as recorded.")
    source: str = Field(description="Where the assumption comes from.")
    evidence_node_id: uuid.UUID | None = Field(
        description="The Evidence Graph node the assumption rests on."
    )

    @classmethod
    def of(cls, assumption: Assumption) -> "AssumptionRead":
        return cls(
            id=assumption.id,
            kind=assumption.kind,
            statement=assumption.statement,
            confidence=assumption.confidence,
            baseline_confidence=assumption.baseline_confidence,
            source=assumption.source,
            evidence_node_id=assumption.evidence_node_id,
        )


class OutcomeRead(BaseModel):
    id: uuid.UUID
    kind: OutcomeKind
    result: str
    impact: float = Field(description="-1 (severe harm) to 1 (objective fully achieved).")
    likelihood: float
    expected_impact: float = Field(description="impact x likelihood.")
    assumption_id: uuid.UUID | None = Field(description="The assumption that drives it.")

    @classmethod
    def of(cls, outcome: Outcome) -> "OutcomeRead":
        return cls(
            id=outcome.id,
            kind=outcome.kind,
            result=outcome.result,
            impact=outcome.impact,
            likelihood=outcome.likelihood,
            expected_impact=outcome.impact * outcome.likelihood,
            assumption_id=outcome.assumption_id,
        )


class CriterionRead(BaseModel):
    value: float = Field(description="The measurement, 0-1, in its natural sense.")
    desirability: float = Field(description="0-1, higher is better.")
    weight: float
    contribution: float = Field(description="desirability x weight; these sum to the score.")


class ScenarioEvidenceRead(BaseModel):
    relied: int = Field(description="Evidence items the scenario acted on.")
    excluded: int = Field(description="Items set aside by the scenario's evidence floor.")
    contradictions: int
    truncated: bool = Field(description="True when more evidence existed than the planner uses.")


class StrategyRead(BaseModel):
    optimism: float = Field(description="-1..1 shift applied to every evidence confidence.")
    contradiction_realization: float = Field(
        description="Multiplier on how likely each contradiction materializes."
    )
    evidence_floor: float = Field(description="Evidence weaker than this is ignored.")
    commitment: float = Field(description="Share of full execution committed to the objective.")
    adherence_hard: float = Field(description="How reliably hard constraints are kept.")
    adherence_soft: float = Field(description="How reliably soft constraints are kept.")


class ScenarioRead(BaseModel):
    id: uuid.UUID
    simulation_id: uuid.UUID
    decision_id: uuid.UUID
    type: ScenarioType
    name: str
    description: str
    objective: str
    score: float = Field(description="0-1 across the weighted criteria.")
    confidence: float = Field(description="How far to trust the scenario's own assumptions.")
    rank: int = Field(description="1 is the recommended scenario of its simulation.")
    success_likelihood: float
    expected_impact: float = Field(description="Sum of every outcome's impact x likelihood.")
    criteria: dict[Criterion, CriterionRead]
    evidence: ScenarioEvidenceRead
    strategy: StrategyRead = Field(description="The stance the scenario applied.")
    assumptions: list[AssumptionRead]
    outcomes: list[OutcomeRead]
    created_at: datetime

    @classmethod
    def of(cls, scenario: Scenario) -> "ScenarioRead":
        scores = scenario.scores
        return cls(
            id=scenario.id,
            simulation_id=scenario.simulation_id,
            decision_id=scenario.decision_id,
            type=scenario.type,
            name=scenario.name,
            description=scenario.description,
            objective=scenario.objective,
            score=scenario.score,
            confidence=scenario.confidence,
            rank=scenario.rank,
            success_likelihood=scores.get("success_likelihood", 0.0),
            expected_impact=scores.get("expected_impact", 0.0),
            criteria={
                Criterion(name): CriterionRead(**values)
                for name, values in scores.get("criteria", {}).items()
            },
            evidence=ScenarioEvidenceRead(**scores["evidence"]),
            strategy=StrategyRead(**scores["strategy"]),
            assumptions=[AssumptionRead.of(a) for a in scenario.assumptions],
            outcomes=[OutcomeRead.of(o) for o in scenario.outcomes],
            created_at=scenario.created_at,
        )


class SimulationRead(BaseModel):
    simulation_id: uuid.UUID
    decision_id: uuid.UUID
    objective: str
    parameters: dict[str, Any] = Field(description="The request that produced the simulation.")
    recommended_id: uuid.UUID = Field(description="The top-ranked scenario.")
    scenarios: list[ScenarioRead] = Field(description="Best first.")
    created_at: datetime

    @classmethod
    def of(cls, scenarios: Sequence[Scenario]) -> "SimulationRead":
        best = min(scenarios, key=lambda s: s.rank)
        return cls(
            simulation_id=best.simulation_id,
            decision_id=best.decision_id,
            objective=best.objective,
            parameters=best.parameters,
            recommended_id=best.id,
            scenarios=[ScenarioRead.of(s) for s in sorted(scenarios, key=lambda s: s.rank)],
            created_at=best.created_at,
        )


class DecisionScenariosResponse(BaseModel):
    decision_id: uuid.UUID
    simulations: list[SimulationRead] = Field(description="Newest first.")
    total: int
    limit: int
    offset: int


class RecommendedRead(BaseModel):
    id: uuid.UUID
    type: ScenarioType
    name: str
    score: float
    confidence: float


class SimulationSummaryRead(BaseModel):
    simulation_id: uuid.UUID
    decision_id: uuid.UUID
    decision_title: str
    objective: str
    scenario_count: int
    recommended: RecommendedRead
    created_at: datetime

    @classmethod
    def of(cls, summary: SimulationSummary) -> "SimulationSummaryRead":
        best = summary.recommended
        return cls(
            simulation_id=best.simulation_id,
            decision_id=best.decision_id,
            decision_title=summary.decision_title,
            objective=best.objective,
            scenario_count=summary.scenario_count,
            recommended=RecommendedRead(
                id=best.id,
                type=best.type,
                name=best.name,
                score=best.score,
                confidence=best.confidence,
            ),
            created_at=best.created_at,
        )


class SimulationListResponse(BaseModel):
    items: list[SimulationSummaryRead]
    total: int
    limit: int
    offset: int
