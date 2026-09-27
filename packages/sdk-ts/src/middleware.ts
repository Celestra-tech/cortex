import type { CortexRequest, Middleware } from "./http";

export interface Logger {
  debug(message: string, fields?: Record<string, unknown>): void;
  warn(message: string, fields?: Record<string, unknown>): void;
}

/**
 * One line per attempt: operation, status, duration, request ID. Never logs
 * headers or bodies, which carry credentials and user content.
 */
export function loggingMiddleware(logger: Logger = console): Middleware {
  return async (request, next) => {
    const started = performance.now();
    const fields = (extra: Record<string, unknown>) => ({
      operation: request.operation,
      method: request.method,
      path: request.url.pathname,
      attempt: request.attempt,
      requestId: request.requestId,
      durationMs: Math.round(performance.now() - started),
      ...extra,
    });
    try {
      const response = await next(request);
      const log = response.ok ? logger.debug : logger.warn;
      log.call(
        logger,
        `cortex ${request.operation} ${response.status}`,
        fields({ status: response.status }),
      );
      return response;
    } catch (error) {
      logger.warn(`cortex ${request.operation} failed`, fields({ error: String(error) }));
      throw error;
    }
  };
}

export interface TelemetryEvent {
  operation: string;
  method: string;
  url: string;
  attempt: number;
  requestId: string;
  durationMs: number;
  /** Null when no response arrived. */
  status: number | null;
  error?: unknown;
}

/** Reports every attempt to your metrics pipeline. The callback must not throw. */
export function telemetryMiddleware(report: (event: TelemetryEvent) => void): Middleware {
  return async (request, next) => {
    const started = performance.now();
    const base = {
      operation: request.operation,
      method: request.method,
      url: request.url.toString(),
      attempt: request.attempt,
      requestId: request.requestId,
    };
    try {
      const response = await next(request);
      report({ ...base, durationMs: performance.now() - started, status: response.status });
      return response;
    } catch (error) {
      report({ ...base, durationMs: performance.now() - started, status: null, error });
      throw error;
    }
  };
}

/** Adds headers to every request; a function is evaluated per attempt. */
export function headersMiddleware(
  headers: Record<string, string> | ((request: CortexRequest) => Record<string, string>),
): Middleware {
  return (request, next) => {
    const values = typeof headers === "function" ? headers(request) : headers;
    for (const [name, value] of Object.entries(values)) request.headers.set(name, value);
    return next(request);
  };
}

export interface TracingOptions {
  /**
   * The active trace context, e.g. from OpenTelemetry. When it returns a
   * `traceparent`, it is propagated unchanged.
   */
  current?: () => { traceparent: string; tracestate?: string } | undefined;
  /** Sampled flag for traces the SDK starts itself. Defaults to true. */
  sampled?: boolean;
}

/**
 * W3C Trace Context propagation. Without an active trace, each call starts
 * one whose trace ID derives from the request ID, so logs on both sides
 * join on either value; each attempt gets its own span ID.
 */
export function tracingMiddleware(options: TracingOptions = {}): Middleware {
  return (request, next) => {
    const context = options.current?.();
    if (context) {
      request.headers.set("traceparent", context.traceparent);
      if (context.tracestate) request.headers.set("tracestate", context.tracestate);
    } else {
      const flags = options.sampled === false ? "00" : "01";
      request.headers.set(
        "traceparent",
        `00-${traceId(request.requestId)}-${randomHex(8)}-${flags}`,
      );
    }
    return next(request);
  };
}

function traceId(requestId: string): string {
  const hex = requestId.replace(/^req_/, "").toLowerCase();
  return /^[0-9a-f]{32}$/.test(hex) && !/^0+$/.test(hex) ? hex : randomHex(16);
}

function randomHex(bytes: number): string {
  const values = crypto.getRandomValues(new Uint8Array(bytes));
  return Array.from(values, (b) => b.toString(16).padStart(2, "0")).join("");
}
