import { describe, expect, it } from "vitest";

import { ResponseValidationError, ValidationError } from "../src";
import { cortex, json, mockFetch } from "./helpers";

const criterion = { value: 0.6, desirability: 0.6, weight: 0.2, contribution: 0.12 };

const scenario = {
  id: "s-1",
  simulation_id: "sim-1",
  decision_id: "c-1",
  type: "best_case",
  name: "Best case",
  description: "Evidence holds up and execution is strong.",
  objective: "Keep the customer",
  score: 0.77,
  confidence: 0.4,
  rank: 1,
  success_likelihood: 0.82,
  expected_impact: 0.5,
  criteria: {
    evidence_quality: criterion,
    uncertainty: criterion,
    constraint_satisfaction: criterion,
    objective_alignment: criterion,
    risk_exposure: criterion,
  },
  evidence: { relied: 2, excluded: 0, contradictions: 1, truncated: false },
  strategy: {
    optimism: 0.5,
    contradiction_realization: 0.5,
    evidence_floor: 0,
    commitment: 0.8,
    adherence_hard: 0.97,
    adherence_soft: 0.9,
  },
  assumptions: [
    {
      id: "a-1",
      kind: "evidence",
      statement: "Refund Policy holds",
      confidence: 0.9,
      baseline_confidence: 0.8,
      source: "cortex.evidence",
      evidence_node_id: "n-2",
    },
  ],
  outcomes: [
    {
      id: "o-1",
      kind: "objective_met",
      result: "The objective is met",
      impact: 0.8,
      likelihood: 0.82,
      expected_impact: 0.656,
      assumption_id: null,
    },
  ],
  created_at: "2026-09-27T12:00:00Z",
};

const simulation = {
  simulation_id: "sim-1",
  decision_id: "c-1",
  objective: "Keep the customer",
  parameters: { risk_tolerance: 0.5 },
  recommended_id: "s-1",
  scenarios: [scenario],
  created_at: "2026-09-27T12:00:00Z",
};

const summary = {
  simulation_id: "sim-1",
  decision_id: "c-1",
  decision_title: "Approve refund",
  objective: "Keep the customer",
  scenario_count: 5,
  recommended: { id: "s-1", type: "best_case", name: "Best case", score: 0.77, confidence: 0.4 },
  created_at: "2026-09-27T12:00:00Z",
};

describe("scenarios", () => {
  it("simulates without retrying", async () => {
    const { fetch, calls } = mockFetch(json(simulation, 201));
    const body = {
      decision_id: "c-1",
      objective: "Keep the customer",
      constraints: [{ statement: "Refund under $500", severity: "hard" as const }],
      assumptions: [{ statement: "Finance approves", confidence: 0.9 }],
      risk_tolerance: 0.3,
      weights: { risk_exposure: 0.4 },
      types: ["best_case" as const, "worst_case" as const],
    };
    const result = await cortex(fetch).scenarios.simulate(body);
    expect(result.scenarios[0]!.assumptions[0]!.evidence_node_id).toBe("n-2");
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url.pathname).toBe("/v2/scenarios");
    expect(calls[0]!.body).toEqual(body);

    const failing = mockFetch(json({ detail: "boom" }, 503));
    await expect(
      cortex(failing.fetch).scenarios.simulate({ decision_id: "c-1" }),
    ).rejects.toThrow();
    expect(failing.calls).toHaveLength(1);
  });

  it("reads a scenario and a decision's simulations", async () => {
    const { fetch, calls } = mockFetch(
      json(scenario),
      json({ decision_id: "c-1", simulations: [simulation], total: 1, limit: 2, offset: 0 }),
    );
    const client = cortex(fetch);
    expect((await client.scenarios.get("s/1")).criteria.risk_exposure.contribution).toBe(0.12);
    const forDecision = await client.scenarios.forDecision("c-1", { limit: 2 });
    expect(forDecision.simulations[0]!.recommended_id).toBe("s-1");
    expect(calls[0]!.url.pathname).toBe("/v2/scenarios/s%2F1");
    expect(calls[1]!.url.pathname).toBe("/v2/decisions/c-1/scenarios");
    expect(calls[1]!.url.search).toBe("?limit=2");
  });

  it("pages through simulations", async () => {
    const { fetch, calls } = mockFetch(
      json({ items: [summary, summary], total: 3, limit: 2, offset: 0 }),
      json({ items: [summary], total: 3, limit: 2, offset: 2 }),
    );
    const seen = [];
    for await (const item of cortex(fetch).scenarios.iter({ limit: 2 })) seen.push(item);
    expect(seen).toHaveLength(3);
    expect(calls[1]!.url.search).toBe("?limit=2&offset=2");
  });

  it("validates input before sending", async () => {
    const { fetch, calls } = mockFetch();
    const client = cortex(fetch);
    for (const body of [
      { decision_id: "c-1", types: ["base_case" as const, "base_case" as const] },
      { decision_id: "c-1", types: [] },
      { decision_id: "c-1", risk_tolerance: 1.5 },
      { decision_id: "c-1", weights: { uncertainty: -1 } },
      { decision_id: "c-1", assumptions: [{ statement: " ", confidence: 0.5 }] },
      { decision_id: "c-1", depth: 11 },
    ]) {
      await expect(client.scenarios.simulate(body)).rejects.toThrow(ValidationError);
    }
    expect(calls).toHaveLength(0);
  });

  it("checks response shapes", async () => {
    const { fetch } = mockFetch(json({ ...scenario, criteria: undefined }));
    await expect(cortex(fetch).scenarios.get("s-1")).rejects.toThrow(ResponseValidationError);
  });
});
