import { CortexError } from "./errors";

export const ORGANIZATION_HEADER = "X-Organization-ID";
export const REQUEST_ID_HEADER = "X-Request-ID";
export const DEFAULT_BASE_URL = "http://localhost:8000";

/** A static key, or a function for keys that rotate (called before every attempt). */
export type ApiKeySource = string | (() => string | Promise<string>);

export interface AuthOptions {
  /** Sent as `Authorization: Bearer <key>`. Defaults to `CORTEX_API_KEY`. */
  apiKey?: ApiKeySource | null;
  /**
   * Sent as `X-Organization-ID`. With an API key it must match the key's
   * organization; without one it names the tenant (development servers only).
   * Defaults to `CORTEX_ORGANIZATION_ID`.
   */
  organizationId?: string | null;
  /**
   * API keys are secrets. Browsers expose them to anyone who opens devtools,
   * so the client refuses to hold one in a browser unless this is set.
   */
  dangerouslyAllowBrowser?: boolean;
}

export function env(name: string): string | undefined {
  const process = (globalThis as { process?: { env?: Record<string, string | undefined> } })
    .process;
  const value = process?.env?.[name];
  return value === "" ? undefined : value;
}

function isBrowser(): boolean {
  return typeof (globalThis as { document?: unknown }).document !== "undefined";
}

/** Resolves credentials once at construction; see `authHeaders` for per-request headers. */
export class Credentials {
  private readonly apiKey: ApiKeySource | null;
  readonly organizationId: string | null;

  constructor(options: AuthOptions = {}) {
    this.apiKey = options.apiKey === undefined ? (env("CORTEX_API_KEY") ?? null) : options.apiKey;
    this.organizationId =
      options.organizationId === undefined
        ? (env("CORTEX_ORGANIZATION_ID") ?? null)
        : options.organizationId;
    if (this.apiKey && isBrowser() && !options.dangerouslyAllowBrowser) {
      throw new CortexError(
        "Refusing to use an API key in a browser, where it would be exposed. " +
          "Call Cortex from your server, or pass `dangerouslyAllowBrowser: true`.",
      );
    }
  }

  get hasApiKey(): boolean {
    return this.apiKey !== null;
  }

  /** The current key, resolving rotating sources; null when none is configured. */
  async key(): Promise<string | null> {
    if (this.apiKey === null) return null;
    const key = typeof this.apiKey === "function" ? await this.apiKey() : this.apiKey;
    if (!key) throw new CortexError("The apiKey function returned an empty key");
    return key;
  }

  async headers(): Promise<Record<string, string>> {
    const headers: Record<string, string> = {};
    const key = await this.key();
    if (key !== null) headers.Authorization = `Bearer ${key}`;
    if (this.organizationId) headers[ORGANIZATION_HEADER] = this.organizationId;
    return headers;
  }
}

/** `req_` + a UUID: unique per logical call and reused across its retries. */
export function createRequestId(): string {
  return `req_${crypto.randomUUID().replaceAll("-", "")}`;
}
