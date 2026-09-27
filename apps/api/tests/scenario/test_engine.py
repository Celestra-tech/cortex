import math
import uuid
from dataclasses import replace

import pytest

from cortex_api.models.assumption import AssumptionKind
from cortex_api.models.evidence_node import EvidenceNodeType
from cortex_api.models.outcome import OutcomeKind
from cortex_api.models.scenario import ScenarioType
from cortex_api.services.scenario.assumptions import (
    Constraint,
    ContradictionItem,
    EvidenceItem,
    Severity,
    StatedAssumption,
    shift,
)
from cortex_api.services.scenario.evaluator import entropy, evaluate
from cortex_api.services.scenario.planner import (
    CONTRADICTION_COST,
    FAILURE_COST,
    STRATEGIES,
    PlanningContext,
    ScenarioPlan,
    noisy_or,
    plan,
    plan_all,
)
from cortex_api.services.scenario.scoring import (
    DEFAULT_WEIGHTS,
    Criterion,
    WeightError,
    rank,
    score,
    weights_for,
)

CHUNK = EvidenceItem(uuid.uuid4(), EvidenceNodeType.CHUNK, "Refund Policy · Refunds", 0.81, 1)
DOCUMENT = EvidenceItem(uuid.uuid4(), EvidenceNodeType.DOCUMENT, "Refund Policy", 0.6, 2)
MEMORY = EvidenceItem(uuid.uuid4(), EvidenceNodeType.MEMORY, "Loyal customer", 0.3, 1)
FRAUD = ContradictionItem(
    uuid.uuid4(), EvidenceNodeType.BENCHMARK, "Fraud model v7", 0.4, "Score elevated"
)
CONTEXT = PlanningContext(
    decision_title="Approve refund",
    objective="Keep the customer",
    evidence=[CHUNK, DOCUMENT, MEMORY],
    contradictions=[FRAUD],
    constraints=[
        Constraint("Refund under $500", Severity.HARD),
        Constraint("Within 14 days", Severity.SOFT),
    ],
    stated=[StatedAssumption("Budget approved", 0.9)],
    request_source="api:key/test",
)


def by_type(context: PlanningContext = CONTEXT) -> dict[ScenarioType, ScenarioPlan]:
    return {p.strategy.type: p for p in plan_all(context)}


class TestAssumptions:
    @pytest.mark.parametrize(
        ("confidence", "optimism", "expected"),
        [(0.6, 0.5, 0.8), (0.6, -0.5, 0.3), (0.6, 0.0, 0.6), (1.0, -0.5, 0.5), (0.0, 0.5, 0.5)],
    )
    def test_shift_is_proportional_and_bounded(
        self, confidence: float, optimism: float, expected: float
    ) -> None:
        assert shift(confidence, optimism) == pytest.approx(expected)

    def test_shift_clamps_out_of_range_inputs(self) -> None:
        assert shift(1.4, 2.0) == 1.0
        assert shift(math.nan, 0.0) == 0.0


class TestGeneration:
    def test_generates_all_five_in_a_stable_order(self) -> None:
        plans = plan_all(CONTEXT)
        assert [p.strategy.type for p in plans] == [
            ScenarioType.BEST_CASE,
            ScenarioType.BASE_CASE,
            ScenarioType.WORST_CASE,
            ScenarioType.AGGRESSIVE,
            ScenarioType.CONSERVATIVE,
        ]
        assert plan_all(CONTEXT) == plans

    def test_subset_of_types(self) -> None:
        plans = plan_all(CONTEXT, [ScenarioType.CONSERVATIVE, ScenarioType.BEST_CASE])
        assert [p.strategy.type for p in plans] == [
            ScenarioType.BEST_CASE,
            ScenarioType.CONSERVATIVE,
        ]

    def test_every_input_becomes_an_assumption_with_a_source(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        kinds = [a.kind for a in base.assumptions]
        assert kinds.count(AssumptionKind.EVIDENCE) == 3
        assert kinds.count(AssumptionKind.CONTRADICTION) == 1
        assert kinds.count(AssumptionKind.CONSTRAINT) == 2
        assert kinds.count(AssumptionKind.STATED) == 1
        assert kinds.count(AssumptionKind.STRATEGY) == 2
        assert all(a.source for a in base.assumptions)
        assert {a.source for a in base.assumptions if a.kind is AssumptionKind.CONSTRAINT} == {
            "api:key/test"
        }

    def test_evidence_assumptions_link_to_their_nodes(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        linked = {
            a.evidence_node_id: a
            for a in base.assumptions
            if a.kind in (AssumptionKind.EVIDENCE, AssumptionKind.CONTRADICTION)
        }
        assert set(linked) == {CHUNK.node_id, DOCUMENT.node_id, MEMORY.node_id, FRAUD.node_id}
        assert linked[CHUNK.node_id].baseline == pytest.approx(0.81)
        assert linked[FRAUD.node_id].confidence == pytest.approx(0.6)

    def test_base_case_takes_evidence_at_face_value(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        estimated = [a for a in base.assumptions if a.kind is not AssumptionKind.CONSTRAINT]
        assert all(a.confidence == pytest.approx(a.baseline) for a in estimated)

    def test_conservative_sets_weak_evidence_aside_and_says_so(self) -> None:
        conservative = plan(CONTEXT, STRATEGIES[ScenarioType.CONSERVATIVE])
        assert conservative.relied == (CHUNK, DOCUMENT)
        assert conservative.excluded == (MEMORY,)
        assert any("1 weaker item set aside" in a.statement for a in conservative.assumptions)

    def test_outcomes_reference_the_assumptions_they_depend_on(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        keys = {a.key for a in base.assumptions}
        linked = [o for o in base.outcomes if o.assumption_key]
        assert {o.kind for o in linked} == {
            OutcomeKind.CONTRADICTION,
            OutcomeKind.CONSTRAINT_BREACH,
        }
        assert all(o.assumption_key in keys for o in linked)

    def test_success_likelihood_is_explained_by_its_assumptions(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        expected = noisy_or([0.81, 0.6, 0.3]) * (1 - 0.4) * 0.9
        assert base.success_likelihood == pytest.approx(expected)
        met, missed = base.outcomes[:2]
        assert met.likelihood + missed.likelihood == pytest.approx(1.0)
        assert missed.impact == pytest.approx(-0.6 * FAILURE_COST)

    def test_no_evidence_means_no_path_to_the_objective(self) -> None:
        empty = replace(CONTEXT, evidence=[], contradictions=[])
        base = plan(empty, STRATEGIES[ScenarioType.BASE_CASE])
        assert base.success_likelihood == 0.0
        assert "no recorded supporting evidence" in base.description

    def test_description_names_the_strongest_evidence_and_main_risk(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        assert "Refund Policy · Refunds" in base.description
        assert "Main risk:" in base.description


class TestOutcomeComparison:
    def test_success_likelihood_orders_best_base_worst(self) -> None:
        plans = by_type()
        best, base, worst = (
            plans[ScenarioType.BEST_CASE],
            plans[ScenarioType.BASE_CASE],
            plans[ScenarioType.WORST_CASE],
        )
        assert best.success_likelihood > base.success_likelihood > worst.success_likelihood
        assert best.expected_impact > base.expected_impact > worst.expected_impact

    def test_aggressive_has_more_upside_and_more_exposure_than_conservative(self) -> None:
        plans = by_type()
        aggressive = evaluate(plans[ScenarioType.AGGRESSIVE])
        conservative = evaluate(plans[ScenarioType.CONSERVATIVE])
        assert aggressive.objective_alignment > conservative.objective_alignment
        assert aggressive.risk_exposure > conservative.risk_exposure
        assert aggressive.constraint_satisfaction < conservative.constraint_satisfaction

    def test_contradiction_cost_scales_with_commitment(self) -> None:
        aggressive = by_type()[ScenarioType.AGGRESSIVE]
        contradiction = next(o for o in aggressive.outcomes if o.kind is OutcomeKind.CONTRADICTION)
        assert contradiction.impact == pytest.approx(-1.0 * CONTRADICTION_COST)
        assert contradiction.likelihood == pytest.approx(0.4 * 1.2)


class TestEvaluation:
    def test_entropy(self) -> None:
        assert entropy(0.5) == pytest.approx(1.0)
        assert entropy(0.0) == entropy(1.0) == 0.0

    def test_criteria_are_normalized(self) -> None:
        for p in plan_all(CONTEXT):
            values = evaluate(p).as_dict()
            assert all(0.0 <= v <= 1.0 for v in values.values()), p.strategy.type

    def test_evidence_quality_rewards_diverse_sources(self) -> None:
        same = replace(
            CONTEXT, evidence=[CHUNK, replace(CHUNK, node_id=uuid.uuid4(), strength=0.6)]
        )
        mixed = replace(CONTEXT, evidence=[CHUNK, DOCUMENT])
        strategy = STRATEGIES[ScenarioType.BASE_CASE]
        assert (
            evaluate(plan(mixed, strategy)).evidence_quality
            > evaluate(plan(same, strategy)).evidence_quality
        )

    def test_no_constraints_are_trivially_satisfied(self) -> None:
        free = replace(CONTEXT, constraints=[])
        assert (
            evaluate(plan(free, STRATEGIES[ScenarioType.AGGRESSIVE])).constraint_satisfaction == 1.0
        )

    def test_hard_constraints_weigh_double(self) -> None:
        base = plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])
        assert evaluate(base).constraint_satisfaction == pytest.approx((2 * 0.9 + 0.75) / 3)


class TestScoring:
    def test_weights_are_normalized(self) -> None:
        weights = weights_for()
        assert sum(weights.values()) == pytest.approx(1.0)
        assert weights == pytest.approx({c: w for c, w in DEFAULT_WEIGHTS.items()})

    def test_risk_tolerance_trades_alignment_for_risk(self) -> None:
        bold, careful = weights_for(risk_tolerance=1.0), weights_for(risk_tolerance=0.0)
        assert bold[Criterion.OBJECTIVE_ALIGNMENT] > careful[Criterion.OBJECTIVE_ALIGNMENT]
        assert bold[Criterion.RISK_EXPOSURE] < careful[Criterion.RISK_EXPOSURE]

    def test_invalid_weights(self) -> None:
        with pytest.raises(WeightError):
            weights_for({Criterion.UNCERTAINTY: -1})
        with pytest.raises(WeightError):
            weights_for(dict.fromkeys(Criterion, 0.0))

    def test_score_is_the_sum_of_contributions(self) -> None:
        card = score(evaluate(plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])), weights_for())
        assert 0.0 <= card.score <= 1.0
        assert card.score == pytest.approx(sum(c.contribution for c in card.criteria.values()))
        risk = card.criteria[Criterion.RISK_EXPOSURE]
        assert risk.desirability == pytest.approx(1 - risk.value)

    def test_default_ranking(self) -> None:
        plans = plan_all(CONTEXT)
        cards = [score(evaluate(p), weights_for()) for p in plans]
        ranked = dict(zip((p.strategy.type for p in plans), rank(cards), strict=True))
        assert ranked[ScenarioType.BEST_CASE] < ranked[ScenarioType.BASE_CASE]
        assert ranked[ScenarioType.BASE_CASE] < ranked[ScenarioType.WORST_CASE]
        assert sorted(ranked.values()) == [1, 2, 3, 4, 5]

    def test_risk_tolerance_changes_which_strategy_wins(self) -> None:
        strategies = [ScenarioType.AGGRESSIVE, ScenarioType.CONSERVATIVE]
        plans = plan_all(replace(CONTEXT, constraints=[]), strategies)

        def winner(tolerance: float, alignment: float) -> ScenarioType:
            weights = weights_for({Criterion.OBJECTIVE_ALIGNMENT: alignment}, tolerance)
            cards = [score(evaluate(p), weights) for p in plans]
            return plans[rank(cards).index(1)].strategy.type

        assert winner(0.0, 0.25) is ScenarioType.CONSERVATIVE
        assert winner(1.0, 1.0) is ScenarioType.AGGRESSIVE

    def test_scenario_confidence_follows_certainty_and_evidence(self) -> None:
        plans = by_type()
        best = score(evaluate(plans[ScenarioType.BEST_CASE]), weights_for())
        worst = score(evaluate(plans[ScenarioType.WORST_CASE]), weights_for())
        assert best.confidence > worst.confidence

    def test_ties_break_on_confidence_then_order(self) -> None:
        card = score(evaluate(plan(CONTEXT, STRATEGIES[ScenarioType.BASE_CASE])), weights_for())
        assert rank([card, card]) == [1, 2]
        assert rank([card, replace(card, confidence=card.confidence + 0.1)]) == [2, 1]
