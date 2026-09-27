import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { Dashboard } from "@/components/dashboard";
import { getServerConfig } from "@/lib/config";
import { buildSystemStatus } from "@/lib/system-status";

test("renders the Cortex title and a card for each connection", () => {
  const status = buildSystemStatus(
    getServerConfig({
      CORTEX_API_URL: "http://api:8000",
      CORTEX_API_PUBLIC_URL: "http://localhost:8000",
    }),
    {
      status: "degraded",
      service: "cortex-api",
      version: "1.0.0-alpha",
      checks: {
        postgres: { status: "up", latency_ms: 1.4, error: null },
        redis: { status: "down", latency_ms: null, error: "ConnectionError" },
      },
    },
    4.2,
    new Date("2026-01-01T12:00:00Z"),
  );

  render(<Dashboard status={status} apiDocsUrl="http://localhost:8000/docs" />);

  expect(screen.getByRole("heading", { level: 1, name: "CELESTRA Cortex" })).toBeInTheDocument();
  expect(screen.getByText("The Intelligence Infrastructure")).toBeInTheDocument();
  expect(screen.getByText("2 of 3 services operational")).toBeInTheDocument();

  const api = screen.getByRole("article", { name: "API connection" });
  const postgres = screen.getByRole("article", { name: "PostgreSQL connection" });
  const redis = screen.getByRole("article", { name: "Redis connection" });

  expect(within(api).getByText("Operational")).toBeInTheDocument();
  expect(within(postgres).getByText("Operational")).toBeInTheDocument();
  expect(within(redis).getByText("Offline")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Executions" })).toHaveAttribute("href", "/executions");
});
