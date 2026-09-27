"""Runs a simulation: evidence in, ranked scenarios out.

The simulator reads the decision's evidence from the Evidence Graph, plans
each requested strategy, evaluates and scores the plans, ranks them against
each other, and stores the scenarios with every assumption and outcome. All
scenarios of one run share a `simulation_id` and a transaction.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.assumption import Assumption
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.models.outcome import Outcome
from cortex_api.models.scenario import Scenario, ScenarioType
from cortex_api.repositories.evidence_repository import Direction, EvidenceRepository
from cortex_api.repositories.scenario_repository import ScenarioRepository
from cortex_api.services.evidence.graph import DEFAULT_DEPTH, EvidenceGraphLoader
from cortex_api.services.evidence.traversal import contradicting_evidence, supporting_evidence
from cortex_api.services.scenario.assumptions import (
    Constraint,
    ContradictionItem,
    EvidenceItem,
    StatedAssumption,
)
from cortex_api.services.scenario.evaluator import evaluate
from cortex_api.services.scenario.planner import PlanningContext, ScenarioPlan, plan_all
from cortex_api.services.scenario.scoring import (
    DEFAULT_RISK_TOLERANCE,
    Criterion,
    ScoreCard,
    rank,
    score,
    weights_for,
)

MAX_EVIDENCE = 12
MAX_CONTRADICTIONS = 5

# The model run that generated a decision is how it was produced, not a reason it is right.
NOT_CORROBORATING = frozenset({EvidenceNodeType.BENCHMARK})


@dataclass(frozen=True)
class SimulationRequest:
    decision_id: uuid.UUID
    objective: str | None = None
    constraints: Sequence[Constraint] = ()
    stated: Sequence[StatedAssumption] = ()
    risk_tolerance: float = DEFAULT_RISK_TOLERANCE
    weights: Mapping[Criterion, float] = field(default_factory=dict)
    types: Sequence[ScenarioType] | None = None
    depth: int = DEFAULT_DEPTH


@dataclass(frozen=True)
class Simulation:
    simulation_id: uuid.UUID
    decision: EvidenceNode
    scenarios: list[Scenario]


class ScenarioSimulator:
    def __init__(self, session: AsyncSession, *, loader: EvidenceGraphLoader | None = None):
        self.session = session
        self.loader = loader or EvidenceGraphLoader(EvidenceRepository(session))
        self.repository = ScenarioRepository(session)

    async def context(
        self, organization_id: uuid.UUID, request: SimulationRequest, source: str
    ) -> tuple[EvidenceNode, PlanningContext, bool]:
        """The decision and what the planner needs to know about it, plus whether the
        evidence was cut short by the graph's edge budget."""
        decision = await self.loader.decision(organization_id, request.decision_id)
        graph = await self.loader.around(
            organization_id, decision, direction=Direction.UPSTREAM, depth=request.depth
        )
        supporting = [
            s
            for s in supporting_evidence(graph, decision.id, max_depth=request.depth)
            if s.node.type not in NOT_CORROBORATING and s.strength > 0
        ][:MAX_EVIDENCE]
        contradictions = sorted(
            contradicting_evidence(graph, decision.id),
            key=lambda c: (-(c.edge.confidence * c.node.confidence), str(c.node.id)),
        )[:MAX_CONTRADICTIONS]
        context = PlanningContext(
            decision_title=decision.title,
            objective=(request.objective or "").strip() or decision.title,
            evidence=[
                EvidenceItem(
                    node_id=s.node.id,
                    type=s.node.type,
                    title=s.node.title,
                    strength=s.strength,
                    depth=s.depth,
                )
                for s in supporting
            ],
            contradictions=[
                ContradictionItem(
                    node_id=c.node.id,
                    type=c.node.type,
                    title=c.node.title,
                    strength=c.edge.confidence * c.node.confidence,
                    explanation=c.edge.explanation,
                )
                for c in contradictions
            ],
            constraints=list(request.constraints),
            stated=list(request.stated),
            request_source=source,
        )
        return decision, context, graph.truncated

    async def simulate(
        self, organization_id: uuid.UUID, request: SimulationRequest, *, source: str
    ) -> Simulation:
        """Plans, scores, ranks, and stages every requested scenario. The caller commits."""
        weights = weights_for(request.weights, request.risk_tolerance)
        decision, context, truncated = await self.context(organization_id, request, source)
        plans = plan_all(context, request.types)
        cards = [score(evaluate(p), weights) for p in plans]
        ranks = rank(cards)

        simulation_id = uuid7()
        parameters = {
            "objective": context.objective,
            "constraints": [
                {"statement": c.statement, "severity": str(c.severity)} for c in context.constraints
            ],
            "assumptions": [
                {"statement": s.statement, "confidence": s.confidence} for s in context.stated
            ],
            "risk_tolerance": request.risk_tolerance,
            "weights": {str(c): w for c, w in weights.items()},
            "types": [str(p.strategy.type) for p in plans],
            "depth": request.depth,
            "source": source,
            "simulated_at": datetime.now(UTC).isoformat(),
        }
        scenarios: list[Scenario] = []
        assumptions: list[Assumption] = []
        outcomes: list[Outcome] = []
        for plan, card, position in zip(plans, cards, ranks, strict=True):
            scenario, rows, results = _rows(
                organization_id,
                simulation_id,
                decision,
                request.decision_id,
                plan,
                card,
                position,
                parameters,
                truncated,
            )
            scenarios.append(scenario)
            assumptions += rows
            outcomes += results
        await self.repository.add_simulation(scenarios, assumptions, outcomes)
        stored = await self.repository.simulation(organization_id, simulation_id)
        return Simulation(simulation_id=simulation_id, decision=decision, scenarios=stored)


def _rows(
    organization_id: uuid.UUID,
    simulation_id: uuid.UUID,
    decision: EvidenceNode,
    decision_id: uuid.UUID,
    plan: ScenarioPlan,
    card: ScoreCard,
    position: int,
    parameters: dict[str, object],
    truncated: bool,
) -> tuple[Scenario, list[Assumption], list[Outcome]]:
    scenario = Scenario(
        id=uuid7(),
        organization_id=organization_id,
        simulation_id=simulation_id,
        decision_node_id=decision.id,
        decision_id=decision_id,
        type=plan.strategy.type,
        name=plan.strategy.name,
        description=plan.description,
        objective=str(parameters["objective"]),
        score=card.score,
        confidence=card.confidence,
        rank=position,
        scores={
            "criteria": {str(c): s.as_dict() for c, s in card.criteria.items()},
            "success_likelihood": plan.success_likelihood,
            "expected_impact": plan.expected_impact,
            "evidence": {
                "relied": len(plan.relied),
                "excluded": len(plan.excluded),
                "contradictions": len(plan.assumptions_of("contradiction")),
                "truncated": truncated,
            },
            "strategy": {
                "optimism": plan.strategy.optimism,
                "contradiction_realization": plan.strategy.contradiction_realization,
                "evidence_floor": plan.strategy.evidence_floor,
                "commitment": plan.strategy.commitment,
                "adherence_hard": plan.strategy.adherence_hard,
                "adherence_soft": plan.strategy.adherence_soft,
            },
        },
        parameters=parameters,
    )
    ids: dict[str, uuid.UUID] = {}
    assumptions = []
    for index, draft in enumerate(plan.assumptions):
        ids[draft.key] = uuid7()
        assumptions.append(
            Assumption(
                id=ids[draft.key],
                organization_id=organization_id,
                scenario_id=scenario.id,
                kind=draft.kind,
                statement=draft.statement,
                confidence=draft.confidence,
                baseline_confidence=draft.baseline,
                source=draft.source,
                evidence_node_id=draft.evidence_node_id,
                position=index,
            )
        )
    outcomes = [
        Outcome(
            id=uuid7(),
            organization_id=organization_id,
            scenario_id=scenario.id,
            kind=draft.kind,
            result=draft.result,
            impact=draft.impact,
            likelihood=draft.likelihood,
            assumption_id=ids.get(draft.assumption_key) if draft.assumption_key else None,
            position=index,
        )
        for index, draft in enumerate(plan.outcomes)
    ]
    return scenario, assumptions, outcomes
