import { describe, expect, it } from "vitest";

import { cortex, json, mockFetch } from "./helpers";

describe("router", () => {
  it("lists models", async () => {
    const model = {
      id: "anthropic/claude-haiku-4-5",
      provider: "anthropic",
      model: "claude-haiku-4-5",
      available: true,
      allowed: true,
    };
    const { fetch, calls } = mockFetch(json({ models: [model] }));
    const { models } = await cortex(fetch).router.models();
    expect(models[0]!.id).toBe("anthropic/claude-haiku-4-5");
    expect(calls[0]!.url.pathname).toBe("/v1/models");
  });

  it("filters executions", async () => {
    const { fetch, calls } = mockFetch(json({ items: [], total: 0, limit: 10, offset: 0 }));
    await cortex(fetch).router.listExecutions({
      success: false,
      provider: "openai",
      completionId: "c-1",
      limit: 10,
    });
    expect(calls[0]!.url.search).toBe("?provider=openai&success=false&completion_id=c-1&limit=10");
  });
});
