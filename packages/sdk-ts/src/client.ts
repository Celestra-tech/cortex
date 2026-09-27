import { ApiKeys } from "./api-keys";
import { type AuthOptions, Credentials, DEFAULT_BASE_URL, env } from "./auth";
import { Chat } from "./chat";
import { Documents } from "./documents";
import { Evidence } from "./evidence";
import { type Middleware, type RetryOptions, Transport } from "./http";
import { Knowledge } from "./knowledge";
import { MemoryResource } from "./memory";
import { Observatory, System } from "./observatory";
import { Router } from "./router";
import { Scenarios } from "./scenarios";

export const SDK_VERSION = "1.0.0-alpha";

export interface CortexOptions extends AuthOptions {
  /** Defaults to `CORTEX_BASE_URL`, then `http://localhost:8000`. */
  baseURL?: string;
  /** Per attempt. Defaults to 60000ms; uploads default to 120000ms. */
  timeoutMs?: number;
  /** Shorthand for `retry.maxRetries`. Defaults to 2. */
  maxRetries?: number;
  retry?: RetryOptions;
  /** Wraps every attempt, outermost first. See `loggingMiddleware` and friends. */
  middleware?: Middleware[];
  /** Sent with every request. */
  headers?: Record<string, string>;
  /** Check response shapes at runtime. Defaults to true. */
  validateResponses?: boolean;
  /** Custom fetch, e.g. for proxies or tests. */
  fetch?: typeof globalThis.fetch;
}

/**
 * The CELESTRA Cortex client.
 *
 * ```ts
 * const cortex = new Cortex({ apiKey: process.env.CORTEX_API_KEY });
 * const answer = await cortex.chat.complete({
 *   messages: [{ role: "user", content: "Hello" }],
 * });
 * ```
 */
export class Cortex {
  readonly chat: Chat;
  readonly memory: MemoryResource;
  readonly knowledge: Knowledge;
  readonly documents: Documents;
  readonly evidence: Evidence;
  readonly scenarios: Scenarios;
  readonly router: Router;
  readonly observatory: Observatory;
  readonly system: System;
  readonly apiKeys: ApiKeys;
  readonly baseURL: string;

  private readonly options: CortexOptions;
  private readonly middleware: Middleware[];

  constructor(options: CortexOptions = {}) {
    this.options = options;
    this.baseURL = (options.baseURL ?? env("CORTEX_BASE_URL") ?? DEFAULT_BASE_URL).replace(
      /\/+$/,
      "",
    );
    this.middleware = [...(options.middleware ?? [])];
    const fetchImpl = options.fetch ?? globalThis.fetch?.bind(globalThis);
    if (!fetchImpl) throw new TypeError("No fetch implementation: pass `fetch` in CortexOptions");

    const transport = new Transport({
      baseURL: this.baseURL,
      credentials: new Credentials(options),
      timeoutMs: options.timeoutMs ?? 60_000,
      retry: {
        maxRetries: options.maxRetries ?? options.retry?.maxRetries ?? 2,
        initialDelayMs: options.retry?.initialDelayMs ?? 500,
        maxDelayMs: options.retry?.maxDelayMs ?? 8_000,
      },
      // Live view: middleware added later with `use()` applies to this client.
      middleware: this.middleware,
      fetch: fetchImpl,
      headers: { "X-Cortex-Client": `cortex-ts/${SDK_VERSION}`, ...options.headers },
      validateResponses: options.validateResponses ?? true,
    });

    this.chat = new Chat(transport);
    this.memory = new MemoryResource(transport);
    this.knowledge = new Knowledge(transport);
    this.documents = new Documents(transport);
    this.evidence = new Evidence(transport);
    this.scenarios = new Scenarios(transport);
    this.router = new Router(transport);
    this.observatory = new Observatory(transport);
    this.system = new System(transport);
    this.apiKeys = new ApiKeys(transport);
  }

  /** Appends middleware to this client. */
  use(...middleware: Middleware[]): this {
    this.middleware.push(...middleware);
    return this;
  }

  /** A new client sharing this one's configuration, with overrides (e.g. another organization). */
  withOptions(overrides: CortexOptions): Cortex {
    return new Cortex({
      ...this.options,
      middleware: this.middleware,
      ...overrides,
      headers: { ...this.options.headers, ...overrides.headers },
    });
  }
}
