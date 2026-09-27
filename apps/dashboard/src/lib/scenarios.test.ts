import { describe, expect, test } from "vitest";

import {
  comparisonRows,
  confidenceData,
  formatImpact,
  formatShift,
  groupAssumptions,
  impactData,
  parseSimulationForm,
} from "@/lib/scenarios";
import { BASE, BEST, WORST } from "@/test/scenario-fixtures";

function form(entries: [string, string][]): FormData {
  const data = new FormData();
  for (const [key, value] of entries) data.append(key, value);
  return data;
}

const ALL_TYPES: [string, string][] = [
  ["type", "best_case"],
  ["type", "base_case"],
  ["type", "worst_case"],
  ["type", "aggressive"],
  ["type", "conservative"],
];

describe("formatting", () => {
  test("signs impact with a true minus", () => {
    expect(formatImpact(0.4249)).toBe("+0.42");
    expect(formatImpact(-0.18)).toBe("−0.18");
    expect(formatImpact(0.001)).toBe("0.00");
  });

  test("describes how far a scenario moves an assumption", () => {
    expect(formatShift({ confidence: 0.9, baseline_confidence: 0.81 })).toBe("+9 pts");
    expect(formatShift({ confidence: 0.41, baseline_confidence: 0.81 })).toBe("−40 pts");
    expect(formatShift({ confidence: 0.6, baseline_confidence: 0.6 })).toBe("as recorded");
  });
});

describe("comparison", () => {
  test("marks the best scenario per row, inverting uncertainty and risk", () => {
    const risky = {
      ...WORST,
      criteria: {
        ...WORST.criteria,
        risk_exposure: { ...WORST.criteria.risk_exposure, value: 0.1, desirability: 0.9 },
      },
    };
    const rows = comparisonRows([BEST, BASE, risky]);
    const byKey = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(rows.map((r) => r.key).slice(0, 4)).toEqual([
      "score",
      "confidence",
      "success_likelihood",
      "expected_impact",
    ]);
    expect(byKey.score!.best).toEqual([BEST.id]);
    expect(byKey.score!.values[0]!.formatted).toBe("0.77");
    expect(byKey.risk_exposure!.best).toEqual([risky.id]);
    expect(byKey.risk_exposure!.values[2]!.formatted).toBe("10%");
    // Ties share the mark.
    expect(byKey.evidence_quality!.best).toHaveLength(3);
  });

  test("a single scenario has nothing to beat", () => {
    expect(comparisonRows([BEST]).every((r) => r.best.length === 0)).toBe(true);
  });
});

describe("chart data", () => {
  test("splits expected impact into upside and downside", () => {
    const [best] = impactData([BEST]);
    expect(best!.name).toBe("Best case");
    expect(best!.upside).toBeCloseTo(0.8 * 0.8);
    expect(best!.downside).toBeCloseTo(-0.48 * 0.2 - 0.1);
    expect(best!.net).toBeCloseTo(BEST.expected_impact);
  });

  test("flags the recommended scenario", () => {
    const data = confidenceData([BEST, BASE], BEST.id);
    expect(data.map((d) => d.recommended)).toEqual([true, false]);
    expect(data[1]).toMatchObject({ name: "Base case", score: 0.61, success: 0.5 });
  });

  test("groups assumptions with stated ones first and the stance last", () => {
    expect(groupAssumptions(BEST.assumptions).map((g) => g.kind)).toEqual([
      "evidence",
      "contradiction",
      "constraint",
      "strategy",
    ]);
  });
});

describe("run form", () => {
  test("reads constraints, assumptions, tolerance, and skips blank rows", () => {
    const parsed = parseSimulationForm(
      form([
        ["objective", "  Keep the customer  "],
        ["constraint", "Refund under $500"],
        ["severity", "hard"],
        ["constraint", "  "],
        ["severity", "soft"],
        ["constraint", "Reply within a day"],
        ["severity", "soft"],
        ["assumption", "Finance approves"],
        ["likelihood", "85"],
        ["assumption", ""],
        ["likelihood", "70"],
        ["risk_tolerance", "30"],
        ...ALL_TYPES,
      ]),
    );
    expect(parsed).toEqual({
      ok: true,
      input: {
        objective: "Keep the customer",
        constraints: [
          { statement: "Refund under $500", severity: "hard" },
          { statement: "Reply within a day", severity: "soft" },
        ],
        assumptions: [{ statement: "Finance approves", confidence: 0.85 }],
        risk_tolerance: 0.3,
      },
    });
  });

  test("sends types only when some are left out", () => {
    const parsed = parseSimulationForm(
      form([
        ["type", "base_case"],
        ["type", "conservative"],
      ]),
    );
    expect(parsed).toMatchObject({ ok: true, input: { types: ["base_case", "conservative"] } });
    expect(parsed.ok && parsed.input.objective).toBeFalsy();
  });

  test.each([
    [[["assumption", "Finance approves"], ["likelihood", "120"], ...ALL_TYPES], /likelihood/],
    [[["assumption", "Finance approves"], ["likelihood", ""], ...ALL_TYPES], /likelihood/],
    [[["risk_tolerance", "-5"], ...ALL_TYPES], /Risk tolerance/],
    [[["type", "moonshot"]], /at least one scenario/],
    [[["objective", "x".repeat(1001)], ...ALL_TYPES], /objective/],
  ] as [[string, string][], RegExp][])("rejects invalid input", (entries, message) => {
    const parsed = parseSimulationForm(form(entries));
    expect(parsed.ok).toBe(false);
    expect(!parsed.ok && parsed.error).toMatch(message);
  });
});
