import { afterEach, describe, expect, it, vi } from "vitest";

import { Cortex, CortexError } from "../src";
import { HELLO, ORG, completion, cortex, json, mockFetch } from "./helpers";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("authentication", () => {
  it("sends the API key as a bearer token", async () => {
    const { fetch, calls } = mockFetch(json({ status: "ok" }));
    await cortex(fetch).system.health();
    expect(calls[0]!.headers.get("authorization")).toBe("Bearer ctx_test_key");
    expect(calls[0]!.headers.has("x-organization-id")).toBe(false);
  });

  it("adds the organization header when configured", async () => {
    const { fetch, calls } = mockFetch(json({ id: ORG }));
    await cortex(fetch, { organizationId: ORG }).observatory.organization();
    expect(calls[0]!.headers.get("x-organization-id")).toBe(ORG);
  });

  it("supports header-only tenancy for development servers", async () => {
    const { fetch, calls } = mockFetch(json({ id: ORG }));
    await cortex(fetch, { apiKey: null, organizationId: ORG }).observatory.organization();
    expect(calls[0]!.headers.has("authorization")).toBe(false);
    expect(calls[0]!.headers.get("x-organization-id")).toBe(ORG);
  });

  it("resolves rotating keys before every attempt", async () => {
    const keys = ["ctx_old", "ctx_new"];
    const { fetch, calls } = mockFetch(json({}, 503), json({ status: "ok" }));
    await cortex(fetch, { apiKey: async () => keys.shift()! }).system.health();
    expect(calls.map((c) => c.headers.get("authorization"))).toEqual([
      "Bearer ctx_old",
      "Bearer ctx_new",
    ]);
  });

  it("reads configuration from the environment", async () => {
    vi.stubEnv("CORTEX_API_KEY", "ctx_from_env");
    vi.stubEnv("CORTEX_BASE_URL", "https://api.example.test/");
    vi.stubEnv("CORTEX_ORGANIZATION_ID", ORG);
    const { fetch, calls } = mockFetch(json({ status: "ok" }));
    const client = new Cortex({ fetch });
    await client.system.health();
    expect(client.baseURL).toBe("https://api.example.test");
    expect(calls[0]!.url.toString()).toBe("https://api.example.test/health");
    expect(calls[0]!.headers.get("authorization")).toBe("Bearer ctx_from_env");
    expect(calls[0]!.headers.get("x-organization-id")).toBe(ORG);
  });

  it("refuses to hold an API key in a browser unless told otherwise", () => {
    vi.stubGlobal("document", {});
    expect(() => new Cortex({ apiKey: "ctx_secret" })).toThrow(CortexError);
    expect(() => new Cortex({ apiKey: "ctx_secret", dangerouslyAllowBrowser: true })).not.toThrow();
    expect(() => new Cortex({ apiKey: null, organizationId: ORG })).not.toThrow();
  });
});

describe("request IDs", () => {
  it("sends one ID per call, reused across retries", async () => {
    const { fetch, calls } = mockFetch(json({}, 503), json(completion()));
    await cortex(fetch).chat.complete(HELLO);
    const [first, second] = calls.map((c) => c.headers.get("x-request-id"));
    expect(first).toMatch(/^req_[0-9a-f]{32}$/);
    expect(second).toBe(first);
  });

  it("differs between calls and accepts a caller-supplied ID", async () => {
    const { fetch, calls } = mockFetch(json({}), json({}), json({}));
    const client = cortex(fetch);
    await client.system.health();
    await client.system.health();
    await client.system.health({ requestId: "trace-42" });
    const ids = calls.map((c) => c.headers.get("x-request-id"));
    expect(ids[0]).not.toBe(ids[1]);
    expect(ids[2]).toBe("trace-42");
  });

  it("identifies the SDK and lets callers add headers", async () => {
    const { fetch, calls } = mockFetch(json({}));
    await cortex(fetch, { headers: { "X-Team": "ops" } }).system.health({
      headers: { "X-Call": "1" },
    });
    const headers = calls[0]!.headers;
    expect(headers.get("x-cortex-client")).toMatch(/^cortex-ts\//);
    expect(headers.get("x-team")).toBe("ops");
    expect(headers.get("x-call")).toBe("1");
  });
});
