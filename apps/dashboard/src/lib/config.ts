import { z } from "zod";

export const DASHBOARD_SERVICE = "cortex-dashboard";
export const DASHBOARD_VERSION = "1.0.0-alpha";

export type DashboardEnvironment = "development" | "test" | "staging" | "production";

export interface ServerConfig {
  env: DashboardEnvironment;
  /** Address the dashboard server uses to reach the API (internal network in Docker). */
  apiUrl: string;
  /** Address shown to operators. */
  apiPublicUrl: string;
  postgresEndpoint: string;
  redisEndpoint: string;
  /** Encrypts the session cookie. Required (32+ characters) in staging and production. */
  sessionSecret: string;
  /** Deployed environments are served over HTTPS only. */
  secureCookies: boolean;
  sessionTtlSeconds: number;
}

export class ConfigurationError extends Error {
  override name = "ConfigurationError";
}

// Development only: sessions survive restarts but are worthless anywhere else.
const DEVELOPMENT_SESSION_SECRET = "cortex-dashboard-development-only-secret";

const url = z.url({ protocol: /^https?$/ }).transform((value) => value.replace(/\/+$/, ""));

const schema = z
  .object({
    DASHBOARD_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
    CORTEX_API_URL: url.default("http://localhost:8000"),
    CORTEX_API_PUBLIC_URL: url.optional(),
    POSTGRES_PORT: z.string().default("5432"),
    REDIS_PORT: z.string().default("6379"),
    DASHBOARD_SESSION_SECRET: z.string().optional(),
    DASHBOARD_SESSION_TTL_HOURS: z.coerce
      .number()
      .int()
      .min(1)
      .max(24 * 30)
      .default(12),
  })
  .superRefine((env, ctx) => {
    const deployed = env.DASHBOARD_ENV === "staging" || env.DASHBOARD_ENV === "production";
    if (!deployed) return;
    if ((env.DASHBOARD_SESSION_SECRET ?? "").length < 32) {
      ctx.addIssue({
        code: "custom",
        path: ["DASHBOARD_SESSION_SECRET"],
        message: "must be set to at least 32 random characters",
      });
    }
    if (env.DASHBOARD_ENV === "production" && !env.CORTEX_API_URL.startsWith("https://")) {
      ctx.addIssue({
        code: "custom",
        path: ["CORTEX_API_URL"],
        message: "must be an https:// URL in production",
      });
    }
  });

/**
 * Read at request time (never at build time), so one build serves every
 * environment and secrets are not needed to compile.
 */
export function getServerConfig(
  env: Record<string, string | undefined> = process.env,
): ServerConfig {
  const parsed = schema.safeParse(env);
  if (!parsed.success) {
    const problems = parsed.error.issues.map((i) => `${i.path.join(".")} ${i.message}`);
    throw new ConfigurationError(`Invalid dashboard configuration:\n- ${problems.join("\n- ")}`);
  }
  const values = parsed.data;
  const deployed = values.DASHBOARD_ENV === "staging" || values.DASHBOARD_ENV === "production";
  return {
    env: values.DASHBOARD_ENV,
    apiUrl: values.CORTEX_API_URL,
    apiPublicUrl: values.CORTEX_API_PUBLIC_URL ?? values.CORTEX_API_URL,
    postgresEndpoint: `localhost:${values.POSTGRES_PORT}`,
    redisEndpoint: `localhost:${values.REDIS_PORT}`,
    sessionSecret: values.DASHBOARD_SESSION_SECRET ?? DEVELOPMENT_SESSION_SECRET,
    secureCookies: deployed,
    sessionTtlSeconds: values.DASHBOARD_SESSION_TTL_HOURS * 3600,
  };
}
