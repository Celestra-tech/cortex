import { describe, expect, it } from "vitest";

import {
  AbortError,
  InternalServerError,
  NetworkError,
  ProviderError,
  RateLimitError,
  TimeoutError,
} from "../src";
import { HELLO, completion, cortex, hang, json, mockFetch } from "./helpers";

describe("automatic retries", () => {
  it("retries transient failures and succeeds", async () => {
    const { fetch, calls } = mockFetch(
      new TypeError("fetch failed"),
      json({ detail: "busy" }, 503),
      json(completion()),
    );
    const answer = await cortex(fetch).chat.complete(HELLO);
    expect(answer.output).toBe("Hello there, operator.");
    expect(calls).toHaveLength(3);
  });

  it("gives up after maxRetries with the last error", async () => {
    const { fetch, calls } = mockFetch(json({}, 429), json({}, 429), json({}, 429));
    const error = await cortex(fetch)
      .chat.complete(HELLO)
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(RateLimitError);
    expect(calls).toHaveLength(3);
  });

  it("honours Retry-After", async () => {
    const { fetch } = mockFetch(json({}, 429, { "retry-after-ms": "30" }), json({}));
    const started = performance.now();
    await cortex(fetch, { retry: { maxDelayMs: 1000 } }).system.health();
    expect(performance.now() - started).toBeGreaterThanOrEqual(25);
  });

  it("caps server hints at maxDelayMs", async () => {
    const { fetch } = mockFetch(json({}, 429, { "retry-after": "3600" }), json({}));
    const started = performance.now();
    await cortex(fetch, { retry: { maxDelayMs: 10 } }).system.health();
    expect(performance.now() - started).toBeLessThan(1000);
  });

  it("does not repeat a non-idempotent POST after a 500", async () => {
    const { fetch, calls } = mockFetch(json({ detail: "boom" }, 500));
    await expect(cortex(fetch).chat.complete(HELLO)).rejects.toBeInstanceOf(InternalServerError);
    expect(calls).toHaveLength(1);
  });

  it("retries reads after a 500", async () => {
    const { fetch, calls } = mockFetch(json({}, 500), json({ models: [] }));
    await cortex(fetch).router.models();
    expect(calls).toHaveLength(2);
  });

  it("never retries when every provider already failed server-side", async () => {
    const { fetch, calls } = mockFetch(
      json({ id: "c1", detail: "All providers failed", attempts: [] }, 502),
    );
    await expect(cortex(fetch).chat.complete(HELLO)).rejects.toBeInstanceOf(ProviderError);
    expect(calls).toHaveLength(1);
  });

  it("times out each attempt and retries only idempotent calls", async () => {
    const reads = mockFetch(hang, json({ status: "ok" }));
    await cortex(reads.fetch, { timeoutMs: 20 }).system.health();
    expect(reads.calls).toHaveLength(2);

    const writes = mockFetch(hang);
    const error = await cortex(writes.fetch, { timeoutMs: 20 })
      .chat.complete(HELLO)
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(TimeoutError);
    expect(error).toBeInstanceOf(NetworkError);
    expect(writes.calls).toHaveLength(1);
  });

  it("respects per-call maxRetries", async () => {
    const { fetch, calls } = mockFetch(json({}, 503));
    await expect(cortex(fetch).system.health({ maxRetries: 0 })).rejects.toBeInstanceOf(
      ProviderError,
    );
    expect(calls).toHaveLength(1);
  });

  it("stops immediately when the caller aborts", async () => {
    const controller = new AbortController();
    const { fetch, calls } = mockFetch((call) => {
      setTimeout(() => controller.abort("user cancelled"), 5);
      return hang(call);
    });
    const error = await cortex(fetch)
      .system.health({ signal: controller.signal })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AbortError);
    expect(calls).toHaveLength(1);
  });

  it("stops during backoff when the caller aborts", async () => {
    const controller = new AbortController();
    const { fetch, calls } = mockFetch(json({}, 503), json({}));
    const client = cortex(fetch, { retry: { initialDelayMs: 5000, maxDelayMs: 5000 } });
    setTimeout(() => controller.abort(), 10);
    await expect(client.system.health({ signal: controller.signal })).rejects.toBeInstanceOf(
      AbortError,
    );
    expect(calls).toHaveLength(1);
  });
});
