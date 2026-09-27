import { describe, expect, it } from "vitest";

import { ValidationError } from "../src";
import { cortex, json, mockFetch } from "./helpers";

const SEARCH = {
  query_id: "q-1",
  query: "refund policy",
  mode: "hybrid",
  results: [
    {
      chunk_id: "ch-1",
      document_id: "doc-1",
      title: "Policies",
      content: "Refunds within 30 days.",
      score: 0.91,
      citation: 1,
    },
  ],
  context: {
    text: "[1] Refunds within 30 days.",
    token_count: 7,
    truncated: false,
    citations: [
      {
        index: 1,
        document_id: "doc-1",
        chunk_ids: ["ch-1"],
        title: "Policies",
        label: "Policies",
        score: 0.91,
        snippet: "Refunds within 30 days.",
      },
    ],
    confidence: { score: 0.8, level: "high" },
  },
  metrics: { latency_ms: 12 },
};

describe("knowledge", () => {
  it("searches with a plain query string", async () => {
    const { fetch, calls } = mockFetch(json(SEARCH));
    const result = await cortex(fetch).knowledge.search("refund policy");
    expect(result.context.citations[0]!.label).toBe("Policies");
    expect(calls[0]!.url.pathname).toBe("/v1/knowledge/search");
    expect(calls[0]!.body).toEqual({ query: "refund policy" });
  });

  it("passes full search options", async () => {
    const { fetch, calls } = mockFetch(json(SEARCH));
    await cortex(fetch).knowledge.search({
      query: "refund policy",
      top_k: 3,
      mode: "keyword",
      filters: { metadata: { team: "billing" } },
    });
    expect(calls[0]!.body).toMatchObject({ top_k: 3, mode: "keyword" });
  });

  it("retries a search after a 5xx because it is read-only", async () => {
    const { fetch, calls } = mockFetch(
      json({ detail: "embedding provider down" }, 502),
      json(SEARCH),
    );
    await cortex(fetch).knowledge.search("refund policy");
    expect(calls).toHaveLength(2);
  });

  it("rejects blank queries before sending", async () => {
    const { fetch, calls } = mockFetch();
    await expect(cortex(fetch).knowledge.search("   ")).rejects.toBeInstanceOf(ValidationError);
    expect(calls).toHaveLength(0);
  });

  it("reads metrics and the query log", async () => {
    const { fetch, calls } = mockFetch(
      json({ window_hours: 6 }),
      json({ items: [], total: 0, limit: 10, offset: 0 }),
      json({ id: "q/1", results: [] }),
    );
    const client = cortex(fetch);
    await client.knowledge.metrics({ windowHours: 6 });
    await client.knowledge.listQueries({ limit: 10 });
    await client.knowledge.getQuery("q/1");
    expect(calls.map((c) => `${c.url.pathname}${c.url.search}`)).toEqual([
      "/v1/knowledge/metrics?window_hours=6",
      "/v1/knowledge/queries?limit=10",
      "/v1/knowledge/queries/q%2F1",
    ]);
  });
});
