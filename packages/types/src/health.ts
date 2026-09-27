/** Mirrors `cortex_api.schemas.health`. Keep both sides in sync. */

export interface HealthResponse {
  status: "healthy";
  service: string;
  version: string;
}

export interface DatabaseHealthResponse {
  status: "healthy" | "unhealthy";
  database: "connected" | "disconnected";
}

export type DependencyName = "postgres" | "redis";

export type DependencyState = "up" | "down";

export interface DependencyCheck {
  status: DependencyState;
  latency_ms: number | null;
  error: string | null;
}

export type ReadinessStatus = "healthy" | "degraded";

export interface ReadinessResponse {
  status: ReadinessStatus;
  service: string;
  version: string;
  checks: Record<DependencyName, DependencyCheck>;
}

export interface RedisHealthResponse {
  status: "healthy" | "unhealthy";
  redis: "connected" | "disconnected";
  latency_ms: number | null;
}

export type ProviderAvailability = "available" | "circuit_open" | "unconfigured";

export interface ProviderHealth {
  name: string;
  status: ProviderAvailability;
  configured: boolean;
  circuit_open: boolean;
  consecutive_failures: number;
  last_error: string | null;
}

export interface ProvidersHealthResponse {
  /** unhealthy: no chat provider can take traffic; degraded: some configured ones cannot. */
  status: "healthy" | "degraded" | "unhealthy";
  available: number;
  providers: ProviderHealth[];
  embeddings: { space: string; configured: boolean };
}
