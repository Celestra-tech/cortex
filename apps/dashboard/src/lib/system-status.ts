import { Cortex, type DependencyCheck, type ReadinessResponse } from "@celestra/cortex-sdk";

import { getServerConfig, type ServerConfig } from "@/lib/config";

export type ConnectionId = "api" | "postgres" | "redis";
export type ConnectionState = "operational" | "offline" | "unknown";

export interface Connection {
  id: ConnectionId;
  name: string;
  description: string;
  endpoint: string;
  state: ConnectionState;
  latencyMs: number | null;
}

export interface SystemStatus {
  connections: Connection[];
  apiVersion: string | null;
  checkedAt: string;
}

const STATUS_TIMEOUT_MS = 3000;

function dependencyState(check: DependencyCheck | undefined): ConnectionState {
  if (!check) return "unknown";
  return check.status === "up" ? "operational" : "offline";
}

export function buildSystemStatus(
  config: ServerConfig,
  readiness: ReadinessResponse | null,
  apiLatencyMs: number | null,
  checkedAt: Date = new Date(),
): SystemStatus {
  const postgres = readiness?.checks.postgres;
  const redis = readiness?.checks.redis;

  return {
    apiVersion: readiness?.version ?? null,
    checkedAt: checkedAt.toISOString(),
    connections: [
      {
        id: "api",
        name: "API",
        description: "FastAPI gateway",
        endpoint: config.apiPublicUrl.replace(/^https?:\/\//, ""),
        state: readiness ? "operational" : "offline",
        latencyMs: apiLatencyMs,
      },
      {
        id: "postgres",
        name: "PostgreSQL",
        description: "Primary datastore",
        endpoint: config.postgresEndpoint,
        state: dependencyState(postgres),
        latencyMs: postgres?.latency_ms ?? null,
      },
      {
        id: "redis",
        name: "Redis",
        description: "Cache and ephemeral state",
        endpoint: config.redisEndpoint,
        state: dependencyState(redis),
        latencyMs: redis?.latency_ms ?? null,
      },
    ],
  };
}

export async function getSystemStatus(): Promise<SystemStatus> {
  const config = getServerConfig();
  // Health endpoints are public; never attach tenant credentials to them.
  const client = new Cortex({
    baseURL: config.apiUrl,
    apiKey: null,
    organizationId: null,
    timeoutMs: STATUS_TIMEOUT_MS,
  });

  const started = performance.now();
  try {
    const readiness = await client.system.readiness();
    const latencyMs = Math.round((performance.now() - started) * 100) / 100;
    return buildSystemStatus(config, readiness, latencyMs);
  } catch {
    return buildSystemStatus(config, null, null);
  }
}
