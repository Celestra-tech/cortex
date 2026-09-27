import type { z } from "zod";

import { Credentials, REQUEST_ID_HEADER, createRequestId } from "./auth";
import {
  AbortError,
  APIError,
  CortexError,
  NetworkError,
  ResponseValidationError,
  TimeoutError,
  errorFromResponse,
  retryAfterMs,
} from "./errors";
import { issuesFromZod } from "./types";

/** One attempt of one call, as middleware sees it. Middleware may mutate `headers`. */
export interface CortexRequest {
  /** Stable name of the SDK method, e.g. `chat.complete`. */
  operation: string;
  method: "GET" | "POST" | "DELETE";
  url: URL;
  headers: Headers;
  body: BodyInit | undefined;
  signal: AbortSignal;
  /** Same for every retry of this call; sent as `X-Request-ID`. */
  requestId: string;
  /** 0 for the first try, 1 for the first retry, and so on. */
  attempt: number;
}

export type Next = (request: CortexRequest) => Promise<Response>;

/**
 * Wraps every attempt, outermost first. Call `next(request)` exactly once to
 * continue. Anything thrown that is not a `CortexError` is treated like a
 * failed fetch: wrapped in `NetworkError` and retried.
 */
export type Middleware = (request: CortexRequest, next: Next) => Promise<Response>;

export interface RetryOptions {
  /** Retries after the first attempt. Defaults to 2. */
  maxRetries?: number;
  /** First backoff delay; doubles each retry with jitter. Defaults to 500ms. */
  initialDelayMs?: number;
  /** Ceiling for one delay, including server `Retry-After` hints. Defaults to 8000ms. */
  maxDelayMs?: number;
}

/** Per-call overrides, accepted as the last argument of every SDK method. */
export interface RequestOptions {
  signal?: AbortSignal;
  /** Per attempt. */
  timeoutMs?: number;
  maxRetries?: number;
  headers?: Record<string, string>;
  /** Supply your own correlation ID instead of a generated one. */
  requestId?: string;
}

export interface TransportOptions {
  baseURL: string;
  credentials: Credentials;
  timeoutMs: number;
  retry: Required<RetryOptions>;
  middleware: Middleware[];
  fetch: typeof globalThis.fetch;
  headers: Record<string, string>;
  validateResponses: boolean;
  /** Test seam for backoff sleeps. */
  sleep?: (ms: number, signal: AbortSignal | undefined) => Promise<void>;
  random?: () => number;
}

export interface CallSpec<T> {
  operation: string;
  method?: "GET" | "POST" | "DELETE";
  path: string;
  query?: Record<string, QueryValue>;
  json?: unknown;
  form?: FormData;
  /**
   * Safe to repeat: reads, deletes, and POSTs without side effects. Only
   * idempotent calls are retried after timeouts and 5xx responses; every call
   * is retried after 429, 503, and connection failures.
   */
  idempotent?: boolean;
  /** Statuses that return a body instead of throwing (health checks answer 503). */
  acceptStatuses?: number[];
  schema?: z.ZodType<T>;
  options?: RequestOptions;
}

type QueryValue = string | number | boolean | undefined | null | readonly (string | number)[];

const ALWAYS_RETRY = new Set([429, 503]);
const RETRY_IF_IDEMPOTENT = new Set([408, 500, 502, 504]);

export class Transport {
  constructor(readonly options: TransportOptions) {}

  /** JSON call: decodes, optionally validates, and maps errors. */
  async request<T>(spec: CallSpec<T>): Promise<T> {
    const response = await this.send(spec);
    const requestId = response.headers.get("x-request-id");
    const body = await readBody(response);
    if (!spec.schema || !this.options.validateResponses || body === undefined) return body as T;
    const parsed = spec.schema.safeParse(body);
    if (parsed.success) return parsed.data;
    const issues = issuesFromZod(parsed.error);
    throw new ResponseValidationError(
      `${spec.operation} returned an unexpected shape (request ${requestId ?? "unknown"}): ` +
        issues.map((i) => `${i.path || "body"}: ${i.message}`).join("; "),
      issues,
      body,
    );
  }

  /** Sends with retries and returns the successful raw `Response` (body unread). */
  async send(spec: CallSpec<unknown>): Promise<Response> {
    const options = spec.options ?? {};
    const method = spec.method ?? "GET";
    const idempotent = spec.idempotent ?? method !== "POST";
    const maxRetries = options.maxRetries ?? this.options.retry.maxRetries;
    const requestId = options.requestId ?? createRequestId();
    const url = this.url(spec.path, spec.query);

    for (let attempt = 0; ; attempt++) {
      if (options.signal?.aborted) throw abortError(spec.operation, options.signal);
      let failure: CortexError;
      let hintMs: number | null = null;
      try {
        const response = await this.attempt(spec, method, url, requestId, attempt);
        if (response.ok || spec.acceptStatuses?.includes(response.status)) return response;
        const body = await readBody(response).catch(() => undefined);
        failure = errorFromResponse(
          response.status,
          body,
          response.headers,
          requestId,
          spec.operation,
        );
        hintMs = retryAfterMs(response.headers);
      } catch (error) {
        if (error instanceof AbortError) throw error;
        failure =
          error instanceof CortexError
            ? error
            : new NetworkError(`${spec.operation} failed: ${String(error)}`, {
                cause: error,
                requestId,
              });
      }
      if (attempt >= maxRetries || !shouldRetry(failure, idempotent)) throw failure;
      await this.backoff(attempt, hintMs, options.signal);
    }
  }

  private async attempt(
    spec: CallSpec<unknown>,
    method: CortexRequest["method"],
    url: URL,
    requestId: string,
    attempt: number,
  ): Promise<Response> {
    const options = spec.options ?? {};
    const timeoutMs = options.timeoutMs ?? this.options.timeoutMs;
    const timeout = AbortSignal.timeout(timeoutMs);
    const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;

    const headers = new Headers({ Accept: "application/json", ...this.options.headers });
    for (const [name, value] of Object.entries(await this.options.credentials.headers())) {
      headers.set(name, value);
    }
    let body: BodyInit | undefined;
    if (spec.form) {
      // fetch sets the multipart Content-Type with its boundary.
      body = spec.form;
    } else if (spec.json !== undefined) {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(spec.json);
    }
    for (const [name, value] of Object.entries(options.headers ?? {})) headers.set(name, value);
    headers.set(REQUEST_ID_HEADER, requestId);

    const request: CortexRequest = {
      operation: spec.operation,
      method,
      url: new URL(url),
      headers,
      body,
      signal,
      requestId,
      attempt,
    };
    const dispatch = this.options.middleware.reduceRight<Next>(
      (next, middleware) => (req) => middleware(req, next),
      (req) =>
        this.options.fetch(req.url, {
          method: req.method,
          headers: req.headers,
          body: req.body,
          signal: req.signal,
          cache: "no-store",
        }),
    );
    try {
      return await dispatch(request);
    } catch (error) {
      if (error instanceof CortexError) throw error;
      if (options.signal?.aborted) throw abortError(spec.operation, options.signal);
      if (timeout.aborted) {
        throw new TimeoutError(`${spec.operation} timed out after ${timeoutMs}ms`, {
          cause: error,
          requestId,
        });
      }
      const reason = error instanceof Error ? error.message : String(error);
      throw new NetworkError(`${spec.operation} failed: ${reason}`, { cause: error, requestId });
    }
  }

  private async backoff(attempt: number, hintMs: number | null, signal?: AbortSignal) {
    const { initialDelayMs, maxDelayMs } = this.options.retry;
    const random = this.options.random ?? Math.random;
    const exponential = initialDelayMs * 2 ** attempt;
    const jittered = exponential / 2 + (exponential / 2) * random();
    const delay = Math.min(maxDelayMs, hintMs ?? jittered);
    await (this.options.sleep ?? sleep)(delay, signal);
  }

  url(path: string, query: Record<string, QueryValue> = {}): URL {
    const url = new URL(`${this.options.baseURL}${path}`);
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null) continue;
      for (const item of Array.isArray(value) ? value : [value]) {
        url.searchParams.append(key, String(item));
      }
    }
    return url;
  }
}

function shouldRetry(error: CortexError, idempotent: boolean): boolean {
  if (error instanceof TimeoutError) return idempotent;
  if (error instanceof NetworkError) return true;
  if (!(error instanceof APIError) || error.status === null) return false;
  return ALWAYS_RETRY.has(error.status) || (idempotent && RETRY_IF_IDEMPOTENT.has(error.status));
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined;
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function abortError(operation: string, signal: AbortSignal): AbortError {
  return new AbortError(`${operation} was aborted`, { cause: signal.reason });
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new AbortError("aborted", { cause: signal.reason }));
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new AbortError("aborted during retry backoff", { cause: signal?.reason }));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}
