import type {
  DatabaseHealthResponse,
  HealthResponse,
  Organization,
  OverviewResponse,
  ProvidersHealthResponse,
  ReadinessResponse,
  RedisHealthResponse,
} from "@celestra/cortex-types";

import { CortexError } from "./errors";
import { CortexEventStream, type EventStreamOptions, eventsUrl } from "./events";
import type { RequestOptions } from "./http";
import { APIResource } from "./resource";

export type LiveEventOptions = Omit<EventStreamOptions, "url" | "apiKey">;

/** Operational views: the organization, aggregate metrics, and live events. */
export class Observatory extends APIResource {
  /** The organization this client's credentials resolve to. */
  organization(options?: RequestOptions): Promise<Organization> {
    return this.transport.request({
      operation: "observatory.organization",
      path: "/v1/organization",
      options,
    });
  }

  /** Request volume, latency, provider mix, and corpus size for the trailing window. */
  overview(
    params: { windowHours?: number } = {},
    options?: RequestOptions,
  ): Promise<OverviewResponse> {
    return this.transport.request({
      operation: "observatory.overview",
      path: "/v1/observatory/overview",
      query: { window_hours: params.windowHours },
      options,
    });
  }

  /**
   * Subscribes to the organization's live event stream and starts it.
   * The API key travels as a WebSocket subprotocol and is re-resolved on every
   * reconnect, so rotating key sources keep working. Without a key the
   * organization must be configured (development servers only).
   */
  async events(options: LiveEventOptions): Promise<CortexEventStream> {
    const { credentials, baseURL } = this.transport.options;
    if (!credentials.hasApiKey && !credentials.organizationId) {
      throw new CortexError("Live events need an apiKey or an organizationId");
    }
    const stream = new CortexEventStream({
      ...options,
      url: eventsUrl(baseURL, credentials.organizationId),
      apiKey: credentials.hasApiKey ? () => credentials.key() : undefined,
    });
    stream.start();
    return stream;
  }
}

/** Liveness and readiness. Unhealthy answers (503) resolve with a body instead of throwing. */
export class System extends APIResource {
  health(options?: RequestOptions): Promise<HealthResponse> {
    return this.transport.request({ operation: "system.health", path: "/health", options });
  }

  readiness(options?: RequestOptions): Promise<ReadinessResponse> {
    return this.transport.request({
      operation: "system.readiness",
      path: "/health/ready",
      acceptStatuses: [503],
      options: { maxRetries: 0, ...options },
    });
  }

  databaseHealth(options?: RequestOptions): Promise<DatabaseHealthResponse> {
    return this.transport.request({
      operation: "system.databaseHealth",
      path: "/health/database",
      acceptStatuses: [503],
      options: { maxRetries: 0, ...options },
    });
  }

  redisHealth(options?: RequestOptions): Promise<RedisHealthResponse> {
    return this.transport.request({
      operation: "system.redisHealth",
      path: "/health/redis",
      acceptStatuses: [503],
      options: { maxRetries: 0, ...options },
    });
  }

  /** Credentials and circuit-breaker state per provider; the API makes no provider calls. */
  providersHealth(options?: RequestOptions): Promise<ProvidersHealthResponse> {
    return this.transport.request({
      operation: "system.providersHealth",
      path: "/health/providers",
      acceptStatuses: [503],
      options: { maxRetries: 0, ...options },
    });
  }
}
