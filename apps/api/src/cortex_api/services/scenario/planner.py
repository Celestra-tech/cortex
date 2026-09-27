"""Turns one decision's evidence into plausible paths under different strategies.

A strategy is a stance, stated in numbers so it can be inspected:

- `optimism` moves every evidence and stated assumption toward certainty or
  doubt (see `assumptions.shift`).
- `contradiction_realization` scales how likely contradicting evidence is to
  prove right.
- `evidence_floor` sets aside evidence weaker than this strength.
- `commitment` is how much of the objective the path pursues: the upside when
  it succeeds and, scaled by `FAILURE_COST`, the loss when it does not.
- `adherence_hard` / `adherence_soft` are the chances each constraint is
  respected along the path.

Planning is deterministic: the same evidence, request, and strategy always
produce the same plan.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from cortex_api.models.outcome import OutcomeKind
from cortex_api.models.scenario import ScenarioType
from cortex_api.services.evidence.provenance import clamp, excerpt
from cortex_api.services.scenario.assumptions import (
    AssumptionDraft,
    Constraint,
    ContradictionItem,
    EvidenceItem,
    Severity,
    StatedAssumption,
    for_constraint,
    for_contradiction,
    for_evidence,
    for_stated,
    realization,
    stipulation,
)

FAILURE_COST = 0.6
"""Share of the committed effort lost when the objective is missed."""
CONTRADICTION_COST = 0.5
"""Share of the committed effort lost when a contradiction proves right."""
BREACH_IMPACT: dict[Severity, float] = {Severity.HARD: 1.0, Severity.SOFT: 0.4}
MAX_OBJECTIVE = 300


@dataclass(frozen=True)
class Strategy:
    type: ScenarioType
    name: str
    stance: str
    optimism: float
    contradiction_realization: float
    evidence_floor: float
    commitment: float
    adherence_hard: float
    adherence_soft: float

    def adherence(self, severity: Severity) -> float:
        return self.adherence_hard if severity is Severity.HARD else self.adherence_soft


STRATEGIES: dict[ScenarioType, Strategy] = {
    ScenarioType.BEST_CASE: Strategy(
        type=ScenarioType.BEST_CASE,
        name="Best case",
        stance="Assumes the evidence holds up better than recorded and objections mostly fade.",
        optimism=0.5,
        contradiction_realization=0.5,
        evidence_floor=0.0,
        commitment=0.8,
        adherence_hard=0.97,
        adherence_soft=0.9,
    ),
    ScenarioType.BASE_CASE: Strategy(
        type=ScenarioType.BASE_CASE,
        name="Base case",
        stance="Takes the evidence at face value, exactly as recorded.",
        optimism=0.0,
        contradiction_realization=1.0,
        evidence_floor=0.0,
        commitment=0.6,
        adherence_hard=0.9,
        adherence_soft=0.75,
    ),
    ScenarioType.WORST_CASE: Strategy(
        type=ScenarioType.WORST_CASE,
        name="Worst case",
        stance="Assumes the evidence is half as reliable as recorded and objections sharpen.",
        optimism=-0.5,
        contradiction_realization=1.5,
        evidence_floor=0.0,
        commitment=0.6,
        adherence_hard=0.8,
        adherence_soft=0.5,
    ),
    ScenarioType.AGGRESSIVE: Strategy(
        type=ScenarioType.AGGRESSIVE,
        name="Aggressive strategy",
        stance="Commits fully and early, acting on every signal including weak ones.",
        optimism=0.1,
        contradiction_realization=1.2,
        evidence_floor=0.0,
        commitment=1.0,
        adherence_hard=0.75,
        adherence_soft=0.45,
    ),
    ScenarioType.CONSERVATIVE: Strategy(
        type=ScenarioType.CONSERVATIVE,
        name="Conservative strategy",
        stance="Moves cautiously, acting only on strong evidence and protecting every constraint.",
        optimism=0.0,
        contradiction_realization=0.8,
        evidence_floor=0.5,
        commitment=0.35,
        adherence_hard=0.99,
        adherence_soft=0.95,
    ),
}

ORDER: tuple[ScenarioType, ...] = tuple(STRATEGIES)


@dataclass(frozen=True)
class PlanningContext:
    decision_title: str
    objective: str
    evidence: Sequence[EvidenceItem]
    contradictions: Sequence[ContradictionItem] = ()
    constraints: Sequence[Constraint] = ()
    stated: Sequence[StatedAssumption] = ()
    request_source: str = "api:organization"


@dataclass(frozen=True)
class OutcomeDraft:
    kind: OutcomeKind
    result: str
    impact: float
    likelihood: float
    assumption_key: str | None = None

    @property
    def expected(self) -> float:
        return self.impact * self.likelihood


@dataclass(frozen=True)
class ScenarioPlan:
    strategy: Strategy
    assumptions: tuple[AssumptionDraft, ...]
    outcomes: tuple[OutcomeDraft, ...]
    relied: tuple[EvidenceItem, ...]
    excluded: tuple[EvidenceItem, ...]
    constraints: tuple[Constraint, ...]
    success_likelihood: float
    description: str = field(default="")

    @property
    def expected_impact(self) -> float:
        return sum(outcome.expected for outcome in self.outcomes)

    def assumptions_of(self, *kinds: str) -> list[AssumptionDraft]:
        return [a for a in self.assumptions if a.kind in kinds]


def noisy_or(probabilities: Iterable[float]) -> float:
    """The chance at least one independent source holds."""
    doubt = 1.0
    for p in probabilities:
        doubt *= 1.0 - clamp(p)
    return 1.0 - doubt


def plan(context: PlanningContext, strategy: Strategy) -> ScenarioPlan:
    relied = tuple(e for e in context.evidence if e.strength >= strategy.evidence_floor)
    excluded = tuple(e for e in context.evidence if e.strength < strategy.evidence_floor)
    base = STRATEGIES[ScenarioType.BASE_CASE]

    stipulations = [
        stipulation(0, strategy.stance),
        stipulation(1, f"Commits {strategy.commitment:.0%} of full execution to the objective."),
    ]
    if strategy.evidence_floor > 0:
        stipulations.append(
            stipulation(
                2,
                f"Acts only on evidence with strength of at least {strategy.evidence_floor:.0%};"
                f" {len(excluded)} weaker item{'' if len(excluded) == 1 else 's'} set aside.",
            )
        )
    evidence = [for_evidence(item, strategy.optimism) for item in relied]
    contradictions = [
        for_contradiction(item, strategy.contradiction_realization)
        for item in context.contradictions
    ]
    constraints = [
        for_constraint(
            i,
            constraint,
            strategy.adherence(constraint.severity),
            base.adherence(constraint.severity),
            context.request_source,
        )
        for i, constraint in enumerate(context.constraints)
    ]
    stated = [
        for_stated(i, item, strategy.optimism, context.request_source)
        for i, item in enumerate(context.stated)
    ]

    # Evidence corroborates independently; every contradiction and stated
    # assumption must hold for the objective to be reached.
    success = noisy_or(a.confidence for a in evidence) * math.prod(
        a.confidence for a in (*contradictions, *stated)
    )
    objective = excerpt(context.objective, MAX_OBJECTIVE)
    outcomes = [
        OutcomeDraft(
            kind=OutcomeKind.OBJECTIVE_MET,
            result=f"Objective achieved: {objective}",
            impact=strategy.commitment,
            likelihood=success,
        ),
        OutcomeDraft(
            kind=OutcomeKind.OBJECTIVE_MISSED,
            result=f"Objective missed; {FAILURE_COST:.0%} of the committed effort is lost.",
            impact=-strategy.commitment * FAILURE_COST,
            likelihood=1.0 - success,
        ),
    ]
    outcomes += [
        OutcomeDraft(
            kind=OutcomeKind.CONTRADICTION,
            result=f"{item.label} proves right.",
            impact=-strategy.commitment * CONTRADICTION_COST,
            likelihood=realization(item, strategy.contradiction_realization),
            assumption_key=assumption.key,
        )
        for item, assumption in zip(context.contradictions, contradictions, strict=True)
    ]
    outcomes += [
        OutcomeDraft(
            kind=OutcomeKind.CONSTRAINT_BREACH,
            result=f"{constraint.severity.capitalize()} constraint breached:"
            f" {constraint.statement}",
            impact=-BREACH_IMPACT[constraint.severity],
            likelihood=1.0 - assumption.confidence,
            assumption_key=assumption.key,
        )
        for constraint, assumption in zip(context.constraints, constraints, strict=True)
    ]

    result = ScenarioPlan(
        strategy=strategy,
        assumptions=(*stipulations, *evidence, *contradictions, *constraints, *stated),
        outcomes=tuple(outcomes),
        relied=relied,
        excluded=excluded,
        constraints=tuple(context.constraints),
        success_likelihood=success,
    )
    return replace(result, description=describe(result, context))


def describe(plan: ScenarioPlan, context: PlanningContext) -> str:
    """The scenario's reasoning in plain language, derived only from the plan."""
    total = len(context.evidence)
    if not plan.relied:
        support = (
            "No recorded evidence meets this scenario's bar, so nothing supports reaching the"
            " objective."
            if total
            else "The decision has no recorded supporting evidence, so nothing supports reaching"
            " the objective."
        )
    else:
        strongest = max(plan.relied, key=lambda e: e.strength)
        support = (
            f"Relies on {len(plan.relied)} of {total} evidence item{'' if total == 1 else 's'},"
            f" led by {strongest.label}; the objective is reached with"
            f" {plan.success_likelihood:.0%} likelihood."
        )
    risks = [
        o for o in plan.outcomes if o.impact < 0 and o.kind is not OutcomeKind.OBJECTIVE_MISSED
    ]
    top = max(risks, key=lambda o: o.likelihood * -o.impact, default=None)
    risk = (
        f" Main risk: {top.result.rstrip('.')} ({top.likelihood:.0%})."
        if top and top.likelihood > 0
        else ""
    )
    return f"{plan.strategy.stance} {support}{risk}"


def plan_all(
    context: PlanningContext, types: Iterable[ScenarioType] | None = None
) -> list[ScenarioPlan]:
    wanted = set(types) if types is not None else set(ORDER)
    return [plan(context, STRATEGIES[t]) for t in ORDER if t in wanted]
