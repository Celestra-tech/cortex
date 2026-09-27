import { describe, expect, it } from "vitest";

import {
  ConflictError,
  NotFoundError,
  PermissionDeniedError,
  ProviderError,
  ResponseValidationError,
  ValidationError,
} from "../src";
import { HELLO, completion, cortex, json, mockFetch } from "./helpers";

describe("chat.complete", () => {
  it("posts the request and returns the completion", async () => {
    const { fetch, calls } = mockFetch(json(completion()));
    const answer = await cortex(fetch).chat.complete({
      ...HELLO,
      model: "claude-haiku-4-5",
      memory: { conversation_id: "conv-1" },
      metadata: { feature: "support" },
    });

    expect(answer.provider).toBe("anthropic");
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url.pathname).toBe("/v1/chat/completions");
    expect(calls[0]!.body).toEqual({
      ...HELLO,
      model: "claude-haiku-4-5",
      memory: { conversation_id: "conv-1" },
      metadata: { feature: "support" },
      stream: false,
    });
  });

  it("rejects invalid input before sending", async () => {
    const { fetch, calls } = mockFetch();
    const client = cortex(fetch);
    const cases = [
      { messages: [] },
      { messages: [{ role: "system" as const, content: "Only rules." }] },
      { ...HELLO, temperature: 3 },
      { messages: [{ role: "robot" as never, content: "beep" }] },
    ];
    for (const params of cases) {
      const error = await client.chat.complete(params).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(ValidationError);
      expect((error as ValidationError).status).toBeNull();
      expect((error as ValidationError).issues.length).toBeGreaterThan(0);
    }
    expect(calls).toHaveLength(0);
  });

  it("checks the response shape", async () => {
    const { fetch } = mockFetch(json({ id: "x", output: 42 }), json({ id: "x", output: 42 }));
    await expect(cortex(fetch).chat.complete(HELLO)).rejects.toBeInstanceOf(
      ResponseValidationError,
    );
    const unchecked = await cortex(fetch, { validateResponses: false }).chat.complete(HELLO);
    expect(unchecked).toEqual({ id: "x", output: 42 });
  });

  it("tolerates additive API changes", async () => {
    const future = { ...completion(), finish_reason: "paused", reasoning_tokens: 12 };
    const { fetch } = mockFetch(json(future));
    await expect(cortex(fetch).chat.complete(HELLO)).resolves.toMatchObject({
      reasoning_tokens: 12,
    });
  });
});

describe("typed errors", () => {
  it("exposes provider attempts when every provider failed", async () => {
    const attempts = [
      { provider: "openai", model: "gpt-4.1", success: false, latency_ms: 30, error: "rate_limit" },
    ];
    const { fetch } = mockFetch(
      json({ id: "c-9", detail: "All providers failed", attempts }, 502, {
        "x-request-id": "req_server",
      }),
    );
    const error = (await cortex(fetch)
      .chat.complete(HELLO)
      .catch((e: unknown) => e)) as ProviderError;
    expect(error).toBeInstanceOf(ProviderError);
    expect(error.completionId).toBe("c-9");
    expect(error.attempts).toEqual(attempts);
    expect(error.requestId).toBe("req_server");
    expect(error.message).toContain("All providers failed");
  });

  it("parses FastAPI validation details", async () => {
    const detail = [
      { loc: ["body", "messages", 0, "content"], msg: "String should have at least 1 character" },
    ];
    const { fetch } = mockFetch(json({ detail }, 422));
    const error = (await cortex(fetch, { validateResponses: false })
      .chat.complete(HELLO)
      .catch((e: unknown) => e)) as ValidationError;
    expect(error).toBeInstanceOf(ValidationError);
    expect(error.status).toBe(422);
    expect(error.issues).toEqual([
      { path: "messages.0.content", message: "String should have at least 1 character" },
    ]);
  });

  it.each([
    [403, PermissionDeniedError],
    [404, NotFoundError],
    [409, ConflictError],
    [415, ValidationError],
  ])("maps %i", async (status, type) => {
    const { fetch } = mockFetch(json({ detail: "nope" }, status));
    await expect(cortex(fetch).chat.complete(HELLO)).rejects.toBeInstanceOf(type);
  });
});
