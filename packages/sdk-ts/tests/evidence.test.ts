import { describe, expect, it } from "vitest";

import { ResponseValidationError, ValidationError } from "../src";
import { cortex, json, mockFetch } from "./helpers";

const node = {
  id: "n-1",
  type: "decision",
  ref_id: "c-1",
  title: "Answer: How long do refunds take?",
  confidence: 0.8,
  created_at: "2026-09-27T12:00:00Z",
  occurred_at: "2026-09-27T12:00:00Z",
  metadata: { model: "gpt-4.1" },
};

const edge = {
  id: "e-1",
  type: "supports",
  from_node_id: "n-2",
  to_node_id: "n-1",
  provenance: {
    confidence: 0.9,
    explanation: "Cited as [1] in the answer",
    source: "cortex.knowledge.citations",
    timestamp: "2026-09-27T12:00:00Z",
  },
  created_at: "2026-09-27T12:00:00Z",
};

const chunk = {
  ...node,
  id: "n-2",
  type: "chunk",
  ref_id: "k-1",
  title: "Refund Policy · Refunds",
};

const evidence = {
  decision: node,
  supporting: [{ node: chunk, depth: 1, path_confidence: 0.9, strength: 0.9, path: ["e-1"] }],
  contradicting: [],
  counts: { chunk: 1 },
};

describe("evidence", () => {
  it("explains a completion by its id", async () => {
    const { fetch, calls } = mockFetch(json(evidence));
    const result = await cortex(fetch).evidence.decision("c-1", { depth: 2 });
    expect(result.supporting[0]!.node.title).toBe("Refund Policy · Refunds");
    expect(calls[0]!.url.pathname).toBe("/v2/evidence/c-1");
    expect(calls[0]!.url.search).toBe("?depth=2");
  });

  it("fetches the graph", async () => {
    const graph = {
      root_id: "n-1",
      depth: 4,
      nodes: [
        { ...node, depth: 0 },
        { ...chunk, depth: -1 },
      ],
      edges: [edge],
      timeline: [
        {
          at: "2026-09-27T12:00:00Z",
          kind: "edge",
          id: "e-1",
          label: "Refund Policy · Refunds supports Answer",
          source: "cortex.knowledge.citations",
          confidence: 0.9,
        },
      ],
      truncated: false,
    };
    const { fetch, calls } = mockFetch(json(graph));
    const result = await cortex(fetch).evidence.graph("c/1");
    expect(result.nodes.map((n) => n.depth)).toEqual([0, -1]);
    expect(calls[0]!.url.pathname).toBe("/v2/evidence/c%2F1/graph");
  });

  it("reads a node and a path", async () => {
    const detail = {
      node: chunk,
      upstream: [],
      downstream: [{ edge, node }],
      decisions: [{ node, depth: 1 }],
    };
    const path = {
      source_id: "n-2",
      target_id: "n-1",
      connected: true,
      edges: [edge],
      nodes: [chunk, node],
    };
    const { fetch, calls } = mockFetch(json(detail), json(path));
    const client = cortex(fetch);
    expect((await client.evidence.node("n-2")).decisions).toHaveLength(1);
    expect(
      (await client.evidence.path({ source: "n-2", target: "n-1", directed: true })).connected,
    ).toBe(true);
    expect(calls[0]!.url.pathname).toBe("/v2/evidence/node/n-2");
    expect(calls[1]!.url.search).toBe("?source=n-2&target=n-1&directed=true");
  });

  it("pages through decisions", async () => {
    const { fetch, calls } = mockFetch(
      json({ items: [node, node], total: 3, limit: 2, offset: 0 }),
      json({ items: [node], total: 3, limit: 2, offset: 2 }),
    );
    const seen = [];
    for await (const decision of cortex(fetch).evidence.iterDecisions({ limit: 2 }))
      seen.push(decision);
    expect(seen).toHaveLength(3);
    expect(calls[1]!.url.search).toBe("?limit=2&offset=2");
  });

  it("records a decision without retrying", async () => {
    const { fetch, calls } = mockFetch(json({ detail: "boom" }, 503));
    const body = {
      title: "Approve refund",
      source: "policy-engine@3",
      evidence: [{ type: "document" as const, title: "Refund Policy", explanation: "In window" }],
    };
    await expect(cortex(fetch).evidence.recordDecision(body)).rejects.toThrow();
    expect(calls).toHaveLength(1);
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.body).toEqual(body);
  });

  it("validates input before sending", async () => {
    const { fetch, calls } = mockFetch();
    const client = cortex(fetch);
    await expect(
      client.evidence.recordDecision({ title: "No evidence", evidence: [] }),
    ).rejects.toThrow(ValidationError);
    await expect(
      client.evidence.recordDecision({
        title: "Bad confidence",
        evidence: [{ type: "memory", title: "m", explanation: "x", relation_confidence: 2 }],
      }),
    ).rejects.toThrow(ValidationError);
    expect(calls).toHaveLength(0);
  });

  it("checks response shapes", async () => {
    const { fetch } = mockFetch(json({ decision: node }));
    await expect(cortex(fetch).evidence.decision("c-1")).rejects.toThrow(ResponseValidationError);
  });
});
