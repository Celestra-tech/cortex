import { describe, expect, it } from "vitest";

import { ConflictError, PermissionDeniedError } from "../src";
import { cortex, json, mockFetch } from "./helpers";

const KEY = {
  id: "01900000-0000-7000-8000-0000000000k1",
  organization_id: "01900000-0000-7000-8000-000000000001",
  name: "backend",
  prefix: "ctx_AbCdEfGh",
  role: "member",
  status: "active",
  created_at: "2026-09-27T12:00:00+00:00",
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  rotated_from_id: null,
};

describe("apiKeys", () => {
  it("creates a key and returns its secret", async () => {
    const { fetch, calls } = mockFetch(json({ ...KEY, secret: "ctx_secret" }, 201));
    const key = await cortex(fetch).apiKeys.create({ name: "backend", expires_in_days: 30 });
    expect(key.secret).toBe("ctx_secret");
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.url.pathname).toBe("/v1/api-keys");
    expect(calls[0]!.body).toEqual({ name: "backend", expires_in_days: 30 });
  });

  it("never retries minting, even on a retryable status", async () => {
    const { fetch, calls } = mockFetch(json({ detail: "unavailable" }, 503));
    await expect(cortex(fetch).apiKeys.create({ name: "x" })).rejects.toThrow();
    expect(calls).toHaveLength(1);
  });

  it("lists, optionally including revoked keys", async () => {
    const { fetch, calls } = mockFetch(json({ items: [KEY] }));
    const { items } = await cortex(fetch).apiKeys.list({ includeRevoked: true });
    expect(items[0]!.prefix).toBe("ctx_AbCdEfGh");
    expect(calls[0]!.url.search).toBe("?include_revoked=true");
  });

  it("rotates with a grace period", async () => {
    const { fetch, calls } = mockFetch(json({ ...KEY, secret: "ctx_new" }, 201));
    await cortex(fetch).apiKeys.rotate(KEY.id, { grace_period_seconds: 0 });
    expect(calls[0]!.url.pathname).toBe(`/v1/api-keys/${KEY.id}/rotate`);
    expect(calls[0]!.body).toEqual({ grace_period_seconds: 0 });
  });

  it("maps role and last-admin errors", async () => {
    const { fetch } = mockFetch(
      json({ detail: "This action requires an admin API key" }, 403),
      json({ detail: "Refusing to revoke the organization's last usable admin key" }, 409),
    );
    const client = cortex(fetch);
    await expect(client.apiKeys.list()).rejects.toBeInstanceOf(PermissionDeniedError);
    await expect(client.apiKeys.revoke(KEY.id)).rejects.toBeInstanceOf(ConflictError);
  });
});

describe("system health", () => {
  it("resolves unhealthy provider reports instead of throwing", async () => {
    const body = {
      status: "unhealthy",
      available: 0,
      providers: [],
      embeddings: { space: "local", configured: true },
    };
    const { fetch, calls } = mockFetch(json(body, 503));
    const report = await cortex(fetch).system.providersHealth();
    expect(report.status).toBe("unhealthy");
    expect(calls[0]!.url.pathname).toBe("/health/providers");
  });
});
