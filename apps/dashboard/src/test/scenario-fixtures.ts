import type {
  Assumption,
  CriterionScore,
  Outcome,
  Scenario,
  ScenarioCriterion,
  ScenarioType,
  Simulation,
} from "@celestra/cortex-sdk";

const AT = "2026-09-27T12:00:00+00:00";
export const DECISION_ID = "0192f7a2-0000-7000-8000-00000000d001";

function criteria(
  values: Record<ScenarioCriterion, [value: number, desirability: number]>,
): Record<ScenarioCriterion, CriterionScore> {
  return Object.fromEntries(
    Object.entries(values).map(([key, [value, desirability]]) => [
      key,
      { value, desirability, weight: 0.2, contribution: desirability * 0.2 },
    ]),
  ) as Record<ScenarioCriterion, CriterionScore>;
}

function assumption(id: string, overrides: Partial<Assumption>): Assumption {
  return {
    id,
    kind: "evidence",
    statement: "Refund Policy holds",
    confidence: 0.8,
    baseline_confidence: 0.8,
    source: "cortex.evidence",
    evidence_node_id: null,
    ...overrides,
  };
}

function outcome(id: string, overrides: Partial<Outcome>): Outcome {
  const impact = overrides.impact ?? 0.6;
  const likelihood = overrides.likelihood ?? 0.5;
  return {
    id,
    kind: "objective_met",
    result: "The objective is met",
    impact,
    likelihood,
    expected_impact: impact * likelihood,
    assumption_id: null,
    ...overrides,
  };
}

function scenario(
  type: ScenarioType,
  rank: number,
  overrides: Partial<Scenario> & { success: number; commitment: number },
): Scenario {
  const { success, commitment, ...rest } = overrides;
  const p = `${type}-`;
  const assumptions = [
    assumption(`${p}a1`, {
      statement: "Refund Policy · Refunds holds",
      baseline_confidence: 0.81,
      confidence: type === "best_case" ? 0.9 : type === "worst_case" ? 0.41 : 0.81,
      evidence_node_id: "node-chunk",
    }),
    assumption(`${p}a2`, {
      kind: "contradiction",
      statement: "Fraud flag on the account does not materialize",
      baseline_confidence: 0.6,
      confidence: 0.6,
      evidence_node_id: "node-fraud",
    }),
    assumption(`${p}a3`, {
      kind: "constraint",
      statement: "Refund stays under $500 (hard)",
      baseline_confidence: 1,
      confidence: 0.9,
      source: "api:key-1",
    }),
    assumption(`${p}a4`, {
      kind: "strategy",
      statement: `Commits ${Math.round(commitment * 100)}% of full execution to the objective.`,
      baseline_confidence: 1,
      confidence: 1,
      source: "cortex.scenario.planner",
    }),
  ];
  const outcomes = [
    outcome(`${p}o1`, { impact: commitment, likelihood: success }),
    outcome(`${p}o2`, {
      kind: "objective_missed",
      result: "The objective is missed",
      impact: -commitment * 0.6,
      likelihood: 1 - success,
    }),
    outcome(`${p}o3`, {
      kind: "constraint_breach",
      result: "Breaks: Refund stays under $500",
      impact: -1,
      likelihood: 0.1,
      assumption_id: `${p}a3`,
    }),
  ];
  return {
    id: `${p}id`,
    simulation_id: "sim-1",
    decision_id: DECISION_ID,
    type,
    name: type,
    description: `The ${type} stance.`,
    objective: "Keep the customer",
    score: 0.5,
    confidence: 0.5,
    rank,
    success_likelihood: success,
    expected_impact: outcomes.reduce((sum, o) => sum + o.expected_impact, 0),
    criteria: criteria({
      evidence_quality: [0.7, 0.7],
      uncertainty: [0.6, 0.4],
      constraint_satisfaction: [0.9, 0.9],
      objective_alignment: [success * commitment, success * commitment],
      risk_exposure: [0.3, 0.7],
    }),
    evidence: { relied: 1, excluded: 0, contradictions: 1, truncated: false },
    strategy: {
      optimism: 0,
      contradiction_realization: 1,
      evidence_floor: 0,
      commitment,
      adherence_hard: 0.9,
      adherence_soft: 0.75,
    },
    assumptions,
    outcomes,
    created_at: AT,
    ...rest,
  };
}

export const BEST = scenario("best_case", 1, {
  success: 0.8,
  commitment: 0.8,
  score: 0.77,
  confidence: 0.45,
});
export const BASE = scenario("base_case", 2, {
  success: 0.5,
  commitment: 0.6,
  score: 0.61,
  confidence: 0.4,
});
export const WORST = scenario("worst_case", 3, {
  success: 0.2,
  commitment: 0.6,
  score: 0.45,
  confidence: 0.3,
  evidence: { relied: 1, excluded: 2, contradictions: 1, truncated: true },
});

export const SIMULATION: Simulation = {
  simulation_id: "sim-1",
  decision_id: DECISION_ID,
  objective: "Keep the customer",
  parameters: {
    risk_tolerance: 0.5,
    constraints: [{ statement: "Refund stays under $500", severity: "hard" }],
    assumptions: [],
    weights: {
      evidence_quality: 0.2,
      uncertainty: 0.15,
      constraint_satisfaction: 0.2,
      objective_alignment: 0.25,
      risk_exposure: 0.2,
    },
  },
  recommended_id: BEST.id,
  scenarios: [BEST, BASE, WORST],
  created_at: AT,
};
