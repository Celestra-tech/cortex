import type {
  Assumption,
  AssumptionKind,
  Scenario,
  ScenarioCriterion,
  ScenarioType,
  SimulationCreate,
} from "@celestra/cortex-types";

export const SCENARIO_TYPES: readonly ScenarioType[] = [
  "best_case",
  "base_case",
  "worst_case",
  "aggressive",
  "conservative",
];

export const SCENARIO_LABELS: Record<ScenarioType, string> = {
  best_case: "Best case",
  base_case: "Base case",
  worst_case: "Worst case",
  aggressive: "Aggressive",
  conservative: "Conservative",
};

export const CRITERIA: readonly {
  key: ScenarioCriterion;
  label: string;
  /** For uncertainty and risk, a lower measurement is the better outcome. */
  lowerIsBetter: boolean;
  help: string;
}[] = [
  {
    key: "evidence_quality",
    label: "Evidence quality",
    lowerIsBetter: false,
    help: "How strong and varied the evidence the scenario acts on is.",
  },
  {
    key: "uncertainty",
    label: "Uncertainty",
    lowerIsBetter: true,
    help: "How close the scenario's assumptions sit to a coin flip.",
  },
  {
    key: "constraint_satisfaction",
    label: "Constraints kept",
    lowerIsBetter: false,
    help: "How reliably the scenario keeps your constraints; hard ones count double.",
  },
  {
    key: "objective_alignment",
    label: "Objective alignment",
    lowerIsBetter: false,
    help: "Chance of success times how fully the scenario commits to the objective.",
  },
  {
    key: "risk_exposure",
    label: "Risk exposure",
    lowerIsBetter: true,
    help: "Chance that at least one harmful outcome occurs, weighted by its severity.",
  },
];

export const ASSUMPTION_KIND_LABELS: Record<AssumptionKind, string> = {
  evidence: "Evidence",
  contradiction: "Contradiction",
  constraint: "Constraint",
  strategy: "Stance",
  stated: "Stated",
};

export function scenarioLabel(type: string): string {
  return SCENARIO_LABELS[type as ScenarioType] ?? type;
}

export function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

/** Impact is signed: `+0.42`, `−0.18` (true minus sign), `0.00`. */
export function formatImpact(value: number): string {
  const rounded = Math.round(value * 100) / 100;
  if (rounded === 0) return "0.00";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(2)}`;
}

export function formatShift(assumption: Pick<Assumption, "confidence" | "baseline_confidence">) {
  const points = Math.round((assumption.confidence - assumption.baseline_confidence) * 100);
  if (points === 0) return "as recorded";
  return `${points > 0 ? "+" : "−"}${Math.abs(points)} pts`;
}

export interface ComparisonRow {
  key: ScenarioCriterion | "score" | "confidence" | "success_likelihood" | "expected_impact";
  label: string;
  help: string;
  /** Per scenario id, in the scenarios' order. */
  values: { id: string; value: number; formatted: string }[];
  /** Ids of the scenarios that do best on this row (ties share it). */
  best: string[];
}

function row(
  key: ComparisonRow["key"],
  label: string,
  help: string,
  scenarios: readonly Scenario[],
  measure: (s: Scenario) => number,
  goodness: (s: Scenario) => number,
  format: (value: number) => string = formatPercent,
): ComparisonRow {
  const values = scenarios.map((s) => ({
    id: s.id,
    value: measure(s),
    formatted: format(measure(s)),
  }));
  const top = Math.max(...scenarios.map(goodness));
  const best =
    scenarios.length > 1 ? scenarios.filter((s) => goodness(s) >= top - 1e-9).map((s) => s.id) : [];
  return { key, label, help, values, best };
}

/** Rows for the comparison table: headline figures, then every criterion. */
export function comparisonRows(scenarios: readonly Scenario[]): ComparisonRow[] {
  return [
    row(
      "score",
      "Score",
      "Weighted sum of every criterion's desirability.",
      scenarios,
      (s) => s.score,
      (s) => s.score,
      (v) => v.toFixed(2),
    ),
    row(
      "confidence",
      "Confidence",
      "How far to trust the scenario's own assumptions.",
      scenarios,
      (s) => s.confidence,
      (s) => s.confidence,
    ),
    row(
      "success_likelihood",
      "Success likelihood",
      "Chance the objective is met if every assumption plays out as stated.",
      scenarios,
      (s) => s.success_likelihood,
      (s) => s.success_likelihood,
    ),
    row(
      "expected_impact",
      "Expected impact",
      "Sum of every outcome's impact times its likelihood, from −1 to +1.",
      scenarios,
      (s) => s.expected_impact,
      (s) => s.expected_impact,
      formatImpact,
    ),
    ...CRITERIA.map(({ key, label, help }) =>
      row(
        key,
        label,
        help,
        scenarios,
        (s) => s.criteria[key]?.value ?? 0,
        (s) => s.criteria[key]?.desirability ?? 0,
      ),
    ),
  ];
}

export interface ImpactDatum {
  id: string;
  name: string;
  /** Expected gain from meeting the objective. */
  upside: number;
  /** Expected harm from every negative outcome, as a negative number. */
  downside: number;
  net: number;
}

/** Upside, downside, and net expected impact per scenario. */
export function impactData(scenarios: readonly Scenario[]): ImpactDatum[] {
  return scenarios.map((s) => {
    let upside = 0;
    let downside = 0;
    for (const outcome of s.outcomes) {
      if (outcome.expected_impact >= 0) upside += outcome.expected_impact;
      else downside += outcome.expected_impact;
    }
    return { id: s.id, name: scenarioLabel(s.type), upside, downside, net: upside + downside };
  });
}

export interface ConfidenceDatum {
  id: string;
  name: string;
  score: number;
  confidence: number;
  success: number;
  recommended: boolean;
}

export function confidenceData(
  scenarios: readonly Scenario[],
  recommendedId: string,
): ConfidenceDatum[] {
  return scenarios.map((s) => ({
    id: s.id,
    name: scenarioLabel(s.type),
    score: s.score,
    confidence: s.confidence,
    success: s.success_likelihood,
    recommended: s.id === recommendedId,
  }));
}

/** Assumptions grouped by kind in a stable reading order, stance last. */
export function groupAssumptions(assumptions: readonly Assumption[]) {
  const order: AssumptionKind[] = ["stated", "evidence", "contradiction", "constraint", "strategy"];
  return order
    .map((kind) => ({ kind, items: assumptions.filter((a) => a.kind === kind) }))
    .filter((group) => group.items.length > 0);
}

// --- Run form -------------------------------------------------------------------------------------

export const MAX_ROWS = 20;

export type SimulationForm =
  { ok: true; input: Omit<SimulationCreate, "decision_id"> } | { ok: false; error: string };

function text(value: FormDataEntryValue | null): string {
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Reads the run form. Rows with an empty statement are skipped, so blank
 * trailing rows are harmless; percentages are whole numbers from 0 to 100.
 */
export function parseSimulationForm(form: FormData): SimulationForm {
  const objective = text(form.get("objective"));
  if (objective.length > 1000)
    return { ok: false, error: "Keep the objective under 1,000 characters." };

  const constraintStatements = form.getAll("constraint").map(text);
  const severities = form.getAll("severity").map(text);
  const constraints: NonNullable<SimulationCreate["constraints"]> = [];
  constraintStatements.forEach((statement, i) => {
    if (statement)
      constraints.push({ statement, severity: severities[i] === "hard" ? "hard" : "soft" });
  });

  const assumptionStatements = form.getAll("assumption").map(text);
  const likelihoods = form.getAll("likelihood").map(text);
  const assumptions: NonNullable<SimulationCreate["assumptions"]> = [];
  for (const [i, statement] of assumptionStatements.entries()) {
    if (!statement) continue;
    const percent = Number(likelihoods[i]);
    if (!likelihoods[i] || !Number.isFinite(percent) || percent < 0 || percent > 100) {
      return { ok: false, error: `Give “${statement}” a likelihood from 0 to 100%.` };
    }
    assumptions.push({ statement, confidence: percent / 100 });
  }
  if ([...constraints, ...assumptions].some((row) => row.statement.length > 500)) {
    return { ok: false, error: "Keep each constraint and assumption under 500 characters." };
  }
  if (constraints.length > MAX_ROWS || assumptions.length > MAX_ROWS) {
    return { ok: false, error: `Up to ${MAX_ROWS} constraints and ${MAX_ROWS} assumptions.` };
  }

  const tolerance = Number(text(form.get("risk_tolerance")) || "50");
  if (!Number.isFinite(tolerance) || tolerance < 0 || tolerance > 100) {
    return { ok: false, error: "Risk tolerance runs from 0 to 100." };
  }

  const types = form
    .getAll("type")
    .map(text)
    .filter((t): t is ScenarioType => (SCENARIO_TYPES as readonly string[]).includes(t));
  if (types.length === 0) return { ok: false, error: "Pick at least one scenario to generate." };

  return {
    ok: true,
    input: {
      ...(objective ? { objective } : {}),
      constraints,
      assumptions,
      risk_tolerance: tolerance / 100,
      ...(types.length < SCENARIO_TYPES.length ? { types: [...new Set(types)] } : {}),
    },
  };
}
