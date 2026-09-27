import type { ModelExecution } from "@celestra/cortex-sdk";
import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";

import { ExecutionsTable } from "@/components/executions-table";

function execution(overrides: Partial<ModelExecution> = {}): ModelExecution {
  return {
    id: "e1",
    organization_id: "o1",
    completion_id: "01900000-0000-7000-8000-00000000c001",
    attempt: 1,
    provider: "openai",
    model: "gpt-4.1",
    routing_mode: "auto",
    is_fallback: false,
    latency_ms: 812.4,
    prompt_tokens: 120,
    completion_tokens: 30,
    cost_estimate: 0.00048,
    success: true,
    finish_reason: "stop",
    error_type: null,
    error: null,
    metadata: {},
    created_at: "2026-09-27T12:00:00+00:00",
    ...overrides,
  };
}

test("renders one row per execution with outcome, latency, tokens, and cost", () => {
  render(
    <ExecutionsTable
      page={{
        items: [
          execution({
            id: "e2",
            attempt: 2,
            provider: "anthropic",
            model: "claude-haiku-4-5",
            is_fallback: true,
          }),
          execution({ success: false, error_type: "server_error", error: "HTTP 500" }),
        ],
        total: 2,
        limit: 50,
        offset: 0,
      }}
    />,
  );
  const rows = screen.getAllByRole("row").slice(1);
  expect(rows).toHaveLength(2);
  expect(within(rows[0]!).getByText("Fallback")).toBeInTheDocument();
  expect(within(rows[0]!).getByText("attempt 2")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("server_error")).toHaveAttribute("title", "HTTP 500");
  expect(within(rows[1]!).getByText("812 ms")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("150")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("$0.00048")).toBeInTheDocument();

  // Only successful executions produced a decision with evidence to open.
  expect(within(rows[0]!).getByRole("link", { name: /Evidence for completion/ })).toHaveAttribute(
    "href",
    "/evidence/01900000-0000-7000-8000-00000000c001",
  );
  expect(within(rows[1]!).queryByRole("link")).toBeNull();
});

test("explains the empty state", () => {
  render(<ExecutionsTable page={{ items: [], total: 0, limit: 50, offset: 0 }} />);
  expect(screen.getByText(/Send a chat completion/)).toBeInTheDocument();
});
