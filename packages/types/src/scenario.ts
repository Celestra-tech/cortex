/**
 * Mirrors `cortex_api.schemas.scenario`. Keep both sides in sync.
 *
 * Scenarios are plans, not forecasts: each one applies an explicit stance to
 * the decision's recorded evidence and states every assumption it makes.
 */
import type { Id, Timestamp } from "./memory";

export type ScenarioType = "best_case" | "base_case" | "worst_case" | "aggressive" | "conservative";

export type AssumptionKind = "evidence" | "contradiction" | "constraint" | "strategy" | "stated";

export type OutcomeKind =
  "objective_met" | "objective_missed" | "contradiction" | "constraint_breach";

export type ConstraintSeverity = "hard" | "soft";

export type ScenarioCriterion =
  | "evidence_quality"
  | "uncertainty"
  | "constraint_satisfaction"
  | "objective_alignment"
  | "risk_exposure";

export interface ConstraintInput {
  statement: string;
  /** `hard` must hold for the decision to stand; `soft` is costly but survivable. Defaults to `soft`. */
  severity?: ConstraintSeverity;
}

export interface StatedAssumptionInput {
  statement: string;
  /** 0..1: how likely the assumption holds today. */
  confidence: number;
}

/** Relative importance of each criterion; omitted ones keep their defaults. */
export type ScenarioWeights = Partial<Record<ScenarioCriterion, number>>;

export interface SimulationCreate {
  /** The decision to plan for: for completions, the completion id. */
  decision_id: Id;
  /** What acting on the decision should achieve. Defaults to the decision title. */
  objective?: string | null;
  constraints?: ConstraintInput[];
  /** Conditions the objective depends on beyond the recorded evidence. */
  assumptions?: StatedAssumptionInput[];
  /** 0 favors avoiding harm, 1 favors reaching the objective. Defaults to 0.5. */
  risk_tolerance?: number;
  weights?: ScenarioWeights | null;
  /** Scenarios to generate; all five by default. */
  types?: ScenarioType[] | null;
  /** Evidence hops to consider, 1-10. */
  depth?: number;
}

export interface Assumption {
  id: Id;
  kind: AssumptionKind;
  statement: string;
  /** What the scenario assumes. */
  confidence: number;
  /** The same assumption as recorded. */
  baseline_confidence: number;
  /** `cortex.evidence`, `cortex.scenario.planner`, or the API key that stated it. */
  source: string;
  /** The Evidence Graph node the assumption rests on. */
  evidence_node_id: Id | null;
}

export interface Outcome {
  id: Id;
  kind: OutcomeKind;
  result: string;
  /** -1 (severe harm) to 1 (objective fully achieved). */
  impact: number;
  likelihood: number;
  /** impact x likelihood. */
  expected_impact: number;
  /** The assumption that drives it. */
  assumption_id: Id | null;
}

export interface CriterionScore {
  /** The measurement, 0..1, in its natural sense (high uncertainty is high). */
  value: number;
  /** 0..1, higher is better. */
  desirability: number;
  weight: number;
  /** desirability x weight; these sum to the score. */
  contribution: number;
}

export interface ScenarioEvidence {
  relied: number;
  excluded: number;
  contradictions: number;
  /** True when more evidence existed than the planner uses. */
  truncated: boolean;
}

/** The stance a scenario applied. */
export interface ScenarioStrategy {
  optimism: number;
  contradiction_realization: number;
  evidence_floor: number;
  commitment: number;
  adherence_hard: number;
  adherence_soft: number;
}

export interface Scenario {
  id: Id;
  simulation_id: Id;
  decision_id: Id;
  type: ScenarioType;
  name: string;
  description: string;
  objective: string;
  /** 0..1 across the weighted criteria. */
  score: number;
  /** How far to trust the scenario's own assumptions. */
  confidence: number;
  /** 1 is the recommended scenario of its simulation. */
  rank: number;
  success_likelihood: number;
  expected_impact: number;
  criteria: Record<ScenarioCriterion, CriterionScore>;
  evidence: ScenarioEvidence;
  strategy: ScenarioStrategy;
  assumptions: Assumption[];
  outcomes: Outcome[];
  created_at: Timestamp;
}

export interface Simulation {
  simulation_id: Id;
  decision_id: Id;
  objective: string;
  /** The request that produced the simulation. */
  parameters: Record<string, unknown>;
  recommended_id: Id;
  /** Best first. */
  scenarios: Scenario[];
  created_at: Timestamp;
}

export interface DecisionScenariosResponse {
  decision_id: Id;
  /** Newest first. */
  simulations: Simulation[];
  total: number;
  limit: number;
  offset: number;
}

export interface SimulationSummary {
  simulation_id: Id;
  decision_id: Id;
  decision_title: string;
  objective: string;
  scenario_count: number;
  recommended: Pick<Scenario, "id" | "type" | "name" | "score" | "confidence">;
  created_at: Timestamp;
}

export interface SimulationListResponse {
  items: SimulationSummary[];
  total: number;
  limit: number;
  offset: number;
}
