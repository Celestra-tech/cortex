import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, expect, test, vi } from "vitest";

import { AssumptionsPanel } from "@/components/scenarios/assumptions-panel";
import { ComparisonTable } from "@/components/scenarios/comparison-table";
import { ConfidenceChart } from "@/components/scenarios/confidence-chart";
import { ImpactChart } from "@/components/scenarios/impact-chart";
import { ScenarioCards } from "@/components/scenarios/scenario-cards";
import { SimulationForm } from "@/components/scenarios/simulation-form";
import { SimulationsTable } from "@/components/scenarios/simulations-table";
import { confidenceData, impactData } from "@/lib/scenarios";
import { BASE, BEST, DECISION_ID, SIMULATION, WORST } from "@/test/scenario-fixtures";

vi.mock("@/app/scenarios/actions", () => ({
  runSimulation: vi.fn(async () => ({ error: null })),
}));

beforeAll(() => {
  // Recharts measures its container; jsdom has no layout.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

test("cards rank scenarios and badge the recommended one", () => {
  render(<ScenarioCards scenarios={SIMULATION.scenarios} recommendedId={BEST.id} />);
  const cards = screen.getAllByRole("article");
  expect(cards).toHaveLength(3);
  expect(within(cards[0]!).getByText("Best case")).toBeInTheDocument();
  expect(within(cards[0]!).getByText("Recommended")).toBeInTheDocument();
  expect(within(cards[0]!).getByText("0.77")).toBeInTheDocument();
  expect(within(cards[1]!).queryByText("Recommended")).not.toBeInTheDocument();
  expect(
    within(cards[0]!).getByRole("meter", { name: "Best case success likelihood" }),
  ).toHaveAttribute("aria-valuenow", "80");
  expect(within(cards[2]!).getByText(/2 set aside/)).toBeInTheDocument();
  expect(within(cards[2]!).getByText(/strongest shown/)).toBeInTheDocument();
});

test("the comparison table has a column per scenario and marks the best values", () => {
  render(<ComparisonTable scenarios={[BEST, BASE, WORST]} />);
  const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
  expect(headers).toEqual(["Criterion", "Best case", "Base case", "Worst case"]);
  const score = screen.getByRole("row", { name: /^Score/ });
  expect(
    within(score)
      .getAllByRole("cell")
      .map((c) => c.textContent),
  ).toEqual(["0.77 (best)", "0.61", "0.45"]);
  expect(screen.getByRole("rowheader", { name: /Risk exposure/ })).toBeInTheDocument();
});

test("the assumptions panel switches scenarios and links evidence", () => {
  render(
    <AssumptionsPanel scenarios={[BEST, WORST]} decisionId={DECISION_ID} initialId={BEST.id} />,
  );
  const best = screen.getByRole("button", { name: "Best case" });
  expect(best).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("+9 pts")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: "View in evidence graph" })[0]).toHaveAttribute(
    "href",
    `/evidence/${DECISION_ID}`,
  );
  expect(screen.getByText("Source: Your request")).toBeInTheDocument();
  expect(screen.getByText(/driven by “Refund stays under \$500 \(hard\)”/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Worst case" }));
  expect(screen.getByRole("button", { name: "Worst case" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(screen.getByText("−40 pts")).toBeInTheDocument();
  const outcomes = screen.getByRole("region", { name: "Worst case outcomes" });
  // Largest expected effect first: missing the objective (−0.36 × 80%).
  expect(within(outcomes).getAllByRole("listitem")[0]).toHaveTextContent("The objective is missed");
});

test("charts carry an accessible table of their data", () => {
  render(
    <>
      <ImpactChart data={impactData([BEST, WORST])} />
      <ConfidenceChart data={confidenceData([BEST, WORST], BEST.id)} />
    </>,
  );
  const impact = screen.getByRole("table", { name: "Expected impact by scenario" });
  expect(within(impact).getByRole("row", { name: /Best case/ })).toHaveTextContent("+0.64");
  const confidence = screen.getByRole("table", { name: /Score, confidence/ });
  expect(within(confidence).getByText("Best case (recommended)")).toBeInTheDocument();
  expect(within(confidence).getByText("30%")).toBeInTheDocument();
});

test("the run form adds and removes rows and keeps every scenario checked", () => {
  render(<SimulationForm decisionId={DECISION_ID} decisionTitle="Approve refund" />);
  expect(screen.getByRole("textbox", { name: /Objective/ })).toHaveAttribute(
    "placeholder",
    "Approve refund",
  );
  fireEvent.click(screen.getByRole("button", { name: "Add constraint" }));
  fireEvent.click(screen.getByRole("button", { name: "Add assumption" }));
  expect(screen.getByRole("textbox", { name: "Constraint 1" })).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "Assumption 1 likelihood (%)" })).toHaveValue(70);
  fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]!);
  expect(screen.queryByRole("textbox", { name: "Constraint 1" })).not.toBeInTheDocument();

  fireEvent.change(screen.getByRole("slider"), { target: { value: "25" } });
  expect(screen.getByText("25%")).toBeInTheDocument();
  expect(screen.getAllByRole("checkbox").every((c) => (c as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole("button", { name: "Run simulation" })).toBeEnabled();
});

test("the simulations table links to each simulation", () => {
  render(
    <SimulationsTable
      page={{
        items: [
          {
            simulation_id: "sim-1",
            decision_id: DECISION_ID,
            decision_title: "Approve refund",
            objective: "Keep the customer",
            scenario_count: 5,
            recommended: {
              id: BEST.id,
              type: "best_case",
              name: "Best case",
              score: 0.77,
              confidence: 0.45,
            },
            created_at: SIMULATION.created_at,
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      }}
    />,
  );
  expect(screen.getByRole("link", { name: "Approve refund" })).toHaveAttribute(
    "href",
    `/scenarios/${DECISION_ID}?simulation=sim-1`,
  );
  expect(screen.getByText("Keep the customer")).toBeInTheDocument();
  expect(screen.getByText("Best case")).toBeInTheDocument();
});

test("an empty simulation list points to Evidence", () => {
  render(<SimulationsTable page={{ items: [], total: 0, limit: 50, offset: 0 }} />);
  expect(screen.getByRole("link", { name: "Evidence" })).toHaveAttribute("href", "/evidence");
});
