import { expect, test } from "vitest";

import { ConfigurationError, getServerConfig } from "@/lib/config";

test("development works with no configuration", () => {
  const config = getServerConfig({});
  expect(config.env).toBe("development");
  expect(config.apiUrl).toBe("http://localhost:8000");
  expect(config.apiPublicUrl).toBe("http://localhost:8000");
  expect(config.secureCookies).toBe(false);
  expect(config.sessionTtlSeconds).toBe(12 * 3600);
});

test("urls are normalized and the public url defaults to the internal one", () => {
  const config = getServerConfig({ CORTEX_API_URL: "https://api.celestra.ai/" });
  expect(config.apiUrl).toBe("https://api.celestra.ai");
  expect(config.apiPublicUrl).toBe("https://api.celestra.ai");
});

test("production requires a strong session secret and an https API", () => {
  expect(() =>
    getServerConfig({ DASHBOARD_ENV: "production", CORTEX_API_URL: "http://api:8000" }),
  ).toThrowError(ConfigurationError);
  try {
    getServerConfig({ DASHBOARD_ENV: "production", CORTEX_API_URL: "http://api:8000" });
  } catch (error) {
    expect(String(error)).toContain("DASHBOARD_SESSION_SECRET");
    expect(String(error)).toContain("CORTEX_API_URL must be an https:// URL");
  }
});

test("deployed environments get secure cookies", () => {
  const config = getServerConfig({
    DASHBOARD_ENV: "staging",
    CORTEX_API_URL: "https://api.staging.celestra.ai",
    DASHBOARD_SESSION_SECRET: "s".repeat(32),
    DASHBOARD_SESSION_TTL_HOURS: "2",
  });
  expect(config.secureCookies).toBe(true);
  expect(config.sessionTtlSeconds).toBe(7200);
});
