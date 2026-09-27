import type { CompletionResponse } from "@celestra/cortex-types";
import { vi } from "vitest";

import { Cortex, type CortexOptions } from "../src";

export interface RecordedCall {
  url: URL;
  method: string;
  headers: Headers;
  /** Parsed JSON, the raw FormData, or undefined. */
  body: unknown;
  signal: AbortSignal | undefined;
}

type Handler = Response | Error | ((call: RecordedCall) => Response | Promise<Response>);

/** A fetch that answers from a queue, recording every call. */
export function mockFetch(...handlers: Handler[]) {
  const calls: RecordedCall[] = [];
  const fetch = vi.fn(async (input: string | URL | Request, init: RequestInit = {}) => {
    const body = init.body;
    const call: RecordedCall = {
      url: new URL(String(input)),
      method: init.method ?? "GET",
      headers: new Headers(init.headers),
      body: typeof body === "string" ? JSON.parse(body) : (body ?? undefined),
      signal: init.signal ?? undefined,
    };
    calls.push(call);
    const handler = handlers.shift();
    if (handler === undefined) throw new Error(`unexpected call ${call.method} ${call.url}`);
    if (handler instanceof Error) throw handler;
    return typeof handler === "function" ? handler(call) : handler;
  });
  return { fetch: fetch as unknown as typeof globalThis.fetch, calls };
}

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return Response.json(body, { status, headers });
}

export function sse(chunks: string[], headers: Record<string, string> = {}): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return new Response(body, {
    headers: { "content-type": "text/event-stream", ...headers },
  });
}

/** A fetch handler that never answers until its signal aborts. */
export function hang(call: RecordedCall): Promise<Response> {
  return new Promise((_, reject) => {
    call.signal?.addEventListener("abort", () => reject(call.signal?.reason), { once: true });
  });
}

export function cortex(fetch: typeof globalThis.fetch, options: CortexOptions = {}): Cortex {
  return new Cortex({
    baseURL: "http://cortex.test/",
    apiKey: "ctx_test_key",
    organizationId: null,
    fetch,
    retry: { initialDelayMs: 1, maxDelayMs: 5 },
    ...options,
  });
}

export const ORG = "01900000-0000-7000-8000-000000000001";

export function completion(overrides: Partial<CompletionResponse> = {}): CompletionResponse {
  return {
    id: "01900000-0000-7000-8000-00000000c001",
    object: "cortex.completion",
    created_at: "2026-09-27T12:00:00+00:00",
    provider: "anthropic",
    model: "claude-haiku-4-5",
    output: "Hello there, operator.",
    latency_ms: 420,
    tokens: { prompt: 12, completion: 5, total: 17 },
    finish_reason: "stop",
    routing_reason: "auto: best balanced score",
    routing_mode: "auto",
    cost_estimate: 0.0001,
    attempts: [
      {
        provider: "anthropic",
        model: "claude-haiku-4-5",
        success: true,
        latency_ms: 420,
        error: null,
      },
    ],
    conversation_id: null,
    knowledge: null,
    ...overrides,
  };
}

export const HELLO = { messages: [{ role: "user" as const, content: "Hello" }] };
