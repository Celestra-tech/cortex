import { describe, expect, it, vi } from "vitest";

import {
  type Middleware,
  type TelemetryEvent,
  headersMiddleware,
  loggingMiddleware,
  telemetryMiddleware,
  tracingMiddleware,
} from "../src";
import { cortex, json, mockFetch } from "./helpers";

describe("middleware", () => {
  it("runs outermost first around every attempt", async () => {
    const order: string[] = [];
    const tag =
      (name: string): Middleware =>
      async (request, next) => {
        order.push(`${name}>${request.attempt}`);
        const response = await next(request);
        order.push(`${name}<${response.status}`);
        return response;
      };
    const { fetch } = mockFetch(json({}, 503), json({}));
    await cortex(fetch, { middleware: [tag("outer"), tag("inner")] }).system.health();
    expect(order).toEqual([
      "outer>0",
      "inner>0",
      "inner<503",
      "outer<503",
      "outer>1",
      "inner>1",
      "inner<200",
      "outer<200",
    ]);
  });

  it("can add headers, statically or per attempt", async () => {
    const { fetch, calls } = mockFetch(json({}));
    const client = cortex(fetch).use(
      headersMiddleware({ "X-Tenant-Region": "eu" }),
      headersMiddleware((request) => ({ "X-Operation": request.operation })),
    );
    await client.system.health();
    expect(calls[0]!.headers.get("x-tenant-region")).toBe("eu");
    expect(calls[0]!.headers.get("x-operation")).toBe("system.health");
  });

  it("reports telemetry for each attempt, including failures", async () => {
    const events: TelemetryEvent[] = [];
    const { fetch } = mockFetch(new TypeError("socket hang up"), json({ models: [] }));
    await cortex(fetch, {
      middleware: [telemetryMiddleware((e) => events.push(e))],
    }).router.models();
    expect(events.map((e) => [e.operation, e.attempt, e.status])).toEqual([
      ["router.models", 0, null],
      ["router.models", 1, 200],
    ]);
    expect(events[0]!.requestId).toBe(events[1]!.requestId);
    expect(events.every((e) => e.durationMs >= 0)).toBe(true);
  });

  it("logs without leaking credentials", async () => {
    const logger = { debug: vi.fn(), warn: vi.fn() };
    const { fetch } = mockFetch(json({ detail: "gone" }, 404), json({}));
    const client = cortex(fetch, { middleware: [loggingMiddleware(logger)] });
    await client.observatory.organization().catch(() => undefined);
    await client.system.health();
    expect(logger.warn).toHaveBeenCalledWith(
      "cortex observatory.organization 404",
      expect.objectContaining({ status: 404, path: "/v1/organization" }),
    );
    expect(logger.debug).toHaveBeenCalledWith("cortex system.health 200", expect.anything());
    expect(JSON.stringify([logger.warn.mock.calls, logger.debug.mock.calls])).not.toContain(
      "ctx_test_key",
    );
  });

  it("propagates W3C trace context", async () => {
    const { fetch, calls } = mockFetch(json({}, 503), json({}), json({}));
    const client = cortex(fetch, { middleware: [tracingMiddleware()] });
    await client.system.health();

    const pattern = /^00-([0-9a-f]{32})-([0-9a-f]{16})-01$/;
    const first = pattern.exec(calls[0]!.headers.get("traceparent") ?? "")!;
    const retried = pattern.exec(calls[1]!.headers.get("traceparent") ?? "")!;
    expect(first[1]).toBe(calls[0]!.headers.get("x-request-id")!.slice(4));
    expect(retried[1]).toBe(first[1]);
    expect(retried[2]).not.toBe(first[2]);

    const active = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    await cortex(fetch, {
      middleware: [tracingMiddleware({ current: () => ({ traceparent: active }) })],
    }).system.health();
    expect(calls[2]!.headers.get("traceparent")).toBe(active);
  });

  it("wraps middleware failures as retryable network errors", async () => {
    let failures = 1;
    const flaky: Middleware = (request, next) => {
      if (failures-- > 0) throw new Error("proxy unavailable");
      return next(request);
    };
    const { fetch, calls } = mockFetch(json({}));
    await cortex(fetch, { middleware: [flaky] }).system.health();
    expect(calls).toHaveLength(1);
  });
});
