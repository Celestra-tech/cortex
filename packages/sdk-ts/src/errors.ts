import type { CompletionAttempt } from "@celestra/cortex-types";

/** Root of every error the SDK throws. `instanceof CortexError` catches them all. */
export class CortexError extends Error {
  override name = "CortexError";
}

export interface ValidationIssue {
  /** Dotted path to the offending field, e.g. `messages.0.content`. */
  path: string;
  message: string;
}

export interface APIErrorInit {
  status: number | null;
  body?: unknown;
  requestId?: string | null;
  headers?: Headers;
}

/** The API answered with an error status (or, for `ValidationError`, the SDK refused to send). */
export class APIError extends CortexError {
  override name = "APIError";
  readonly status: number | null;
  readonly body: unknown;
  /** Quote this when reporting a problem; it matches the server's `X-Request-ID`. */
  readonly requestId: string | null;
  readonly headers: Headers | undefined;

  constructor(message: string, init: APIErrorInit) {
    super(message);
    this.status = init.status;
    this.body = init.body;
    this.requestId = init.requestId ?? null;
    this.headers = init.headers;
  }

  /** The server's `detail`, when it is a string. */
  get detail(): string | undefined {
    const detail = (this.body as { detail?: unknown } | undefined)?.detail;
    return typeof detail === "string" ? detail : undefined;
  }
}

/** 401: missing, malformed, invalid, or revoked API key. */
export class AuthenticationError extends APIError {
  override name = "AuthenticationError";
}

/** 403: the key's organization differs from the one requested, or policy forbids the model. */
export class PermissionDeniedError extends APIError {
  override name = "PermissionDeniedError";
}

export class NotFoundError extends APIError {
  override name = "NotFoundError";
}

/** 409. For duplicate documents, `documentId` is the existing copy. */
export class ConflictError extends APIError {
  override name = "ConflictError";

  get documentId(): string | undefined {
    const id = (this.body as { document_id?: unknown } | undefined)?.document_id;
    return typeof id === "string" ? id : undefined;
  }
}

/**
 * The request is invalid: rejected by the server (400, 413, 415, 422) or, with
 * `status: null`, caught by the SDK before anything was sent.
 */
export class ValidationError extends APIError {
  override name = "ValidationError";
  readonly issues: ValidationIssue[];

  constructor(message: string, init: APIErrorInit & { issues?: ValidationIssue[] }) {
    super(message, init);
    this.issues = init.issues ?? issuesFromBody(init.body);
  }
}

/** 429. The SDK already retried; `retryAfterMs` is the server's latest hint. */
export class RateLimitError extends APIError {
  override name = "RateLimitError";

  get retryAfterMs(): number | null {
    return this.headers ? retryAfterMs(this.headers) : null;
  }
}

/**
 * 502/503: no model provider could serve the request. For completions, the
 * server has already retried and fallen back across providers; `attempts`
 * lists every provider call it made.
 */
export class ProviderError extends APIError {
  override name = "ProviderError";

  get completionId(): string | undefined {
    const id = (this.body as { id?: unknown } | undefined)?.id;
    return typeof id === "string" ? id : undefined;
  }

  get attempts(): CompletionAttempt[] {
    const attempts = (this.body as { attempts?: unknown } | undefined)?.attempts;
    return Array.isArray(attempts) ? (attempts as CompletionAttempt[]) : [];
  }
}

/** Any other 5xx. */
export class InternalServerError extends APIError {
  override name = "InternalServerError";
}

/** The request never produced a response: DNS, connection, TLS, or a dropped stream. */
export class NetworkError extends CortexError {
  override name = "NetworkError";
  readonly requestId: string | null;

  constructor(message: string, options: { cause?: unknown; requestId?: string | null } = {}) {
    super(message, { cause: options.cause });
    this.requestId = options.requestId ?? null;
  }
}

/** No response within `timeoutMs`. */
export class TimeoutError extends NetworkError {
  override name = "TimeoutError";
}

/** The caller's `AbortSignal` fired. Never retried. */
export class AbortError extends CortexError {
  override name = "AbortError";
}

/** The API answered successfully but not in the documented shape. */
export class ResponseValidationError extends CortexError {
  override name = "ResponseValidationError";

  constructor(
    message: string,
    readonly issues: ValidationIssue[],
    readonly body: unknown,
  ) {
    super(message);
  }
}

/** Maps an error response to the most specific error class. */
export function errorFromResponse(
  status: number,
  body: unknown,
  headers: Headers,
  requestId: string | null,
  operation: string,
): APIError {
  const init = { status, body, headers, requestId: headers.get("x-request-id") ?? requestId };
  const detail = describe(body);
  const message = `${operation} failed with ${status}${detail ? `: ${detail}` : ""}`;
  if (status === 401) return new AuthenticationError(message, init);
  if (status === 403) return new PermissionDeniedError(message, init);
  if (status === 404) return new NotFoundError(message, init);
  if (status === 409) return new ConflictError(message, init);
  if (status === 429) return new RateLimitError(message, init);
  if ([400, 413, 415, 422].includes(status)) return new ValidationError(message, init);
  if (status === 502 || status === 503) return new ProviderError(message, init);
  if (status >= 500) return new InternalServerError(message, init);
  return new APIError(message, init);
}

/** `Retry-After` (seconds or HTTP date) or `retry-after-ms`, in milliseconds. */
export function retryAfterMs(headers: Headers): number | null {
  const ms = Number(headers.get("retry-after-ms"));
  if (headers.has("retry-after-ms") && Number.isFinite(ms) && ms >= 0) return ms;
  const value = headers.get("retry-after");
  if (value === null) return null;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const date = Date.parse(value);
  return Number.isNaN(date) ? null : Math.max(0, date - Date.now());
}

function describe(body: unknown): string | undefined {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  const issues = issuesFromBody(body);
  if (issues.length) return issues.map((i) => `${i.path || "body"}: ${i.message}`).join("; ");
  return undefined;
}

/** FastAPI's `{"detail": [{"loc": [...], "msg": "..."}]}` validation shape. */
function issuesFromBody(body: unknown): ValidationIssue[] {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (!Array.isArray(detail)) return [];
  return detail.flatMap((item) => {
    if (typeof item !== "object" || item === null) return [];
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    const path = Array.isArray(loc) ? loc.filter((p) => p !== "body").join(".") : "";
    return typeof msg === "string" ? [{ path, message: msg }] : [];
  });
}
