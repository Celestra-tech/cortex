import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { NodeInspection } from "@/app/evidence/actions";
import { DecisionsTable } from "@/components/evidence/decisions-table";
import { EdgeInspector, NodeInspector } from "@/components/evidence/evidence-inspector";
import { ContradictionList, SupportingEvidenceList } from "@/components/evidence/evidence-lists";
import { ProvenanceTimeline } from "@/components/evidence/provenance-timeline";
import {
  CHUNK,
  CHUNKED,
  CITED,
  DECISION,
  DOCUMENT,
  GRAPH,
  MEMORY,
  RECALLED,
} from "@/test/evidence-fixtures";

test("lists decisions with links to their evidence and a confidence meter", () => {
  render(
    <DecisionsTable
      page={{
        items: [
          DECISION,
          {
            ...DECISION,
            id: "d2",
            ref_id: "r2",
            title: "Approve refund",
            metadata: { kind: "external" },
          },
        ],
        total: 2,
        limit: 50,
        offset: 0,
      }}
    />,
  );
  expect(screen.getByRole("link", { name: "Answer: refunds?" })).toHaveAttribute(
    "href",
    `/evidence/${DECISION.ref_id}`,
  );
  expect(screen.getByText("openai/gpt-4.1")).toBeInTheDocument();
  expect(screen.getByText("Recorded via API")).toBeInTheDocument();
  expect(screen.getAllByRole("meter")[0]).toHaveAttribute("aria-valuenow", "82");
});

test("explains an empty decision list", () => {
  render(<DecisionsTable page={{ items: [], total: 0, limit: 50, offset: 0 }} />);
  expect(screen.getByText(/No decisions yet/)).toBeInTheDocument();
});

test("ranks supporting evidence and shows contradictions with their explanation", () => {
  render(
    <>
      <SupportingEvidenceList
        items={[
          { node: CHUNK, depth: 1, path_confidence: 0.9, strength: 0.81, path: [CITED.id] },
          {
            node: DOCUMENT,
            depth: 2,
            path_confidence: 0.81,
            strength: 0.81,
            path: [CHUNKED.id, CITED.id],
          },
        ]}
      />
      <ContradictionList items={[{ node: MEMORY, edge: RECALLED }]} />
    </>,
  );
  const supporting = screen.getByRole("list", { name: /Supporting evidence/ });
  expect(within(supporting).getByText(/direct/)).toBeInTheDocument();
  expect(within(supporting).getByText(/2 hops away/)).toBeInTheDocument();
  expect(within(supporting).getAllByRole("meter")[0]).toHaveAttribute("aria-valuenow", "81");
  expect(screen.getByText("Asked for phone")).toBeInTheDocument();
});

test("says so when nothing supports a decision", () => {
  render(<SupportingEvidenceList items={[]} />);
  expect(screen.getByText(/confidence is 0%/)).toBeInTheDocument();
});

test("renders the provenance timeline in order with sources", () => {
  render(<ProvenanceTimeline events={GRAPH.timeline} />);
  const items = within(screen.getByRole("list", { name: "Provenance timeline" })).getAllByRole(
    "listitem",
  );
  expect(items).toHaveLength(2);
  expect(items[0]).toHaveTextContent("Recorded");
  expect(items[1]).toHaveTextContent("Linked by cortex.knowledge.citations");
  expect(items[1]).toHaveTextContent("90%");
});

test("inspects a node from the graph, then enriches it from the API", async () => {
  const inspect = vi.fn(async (): Promise<NodeInspection> => ({
    ok: true,
    detail: {
      node: CHUNK,
      upstream: [{ edge: CHUNKED, node: DOCUMENT }],
      downstream: [{ edge: CITED, node: DECISION }],
      decisions: [
        { node: DECISION, depth: 1 },
        {
          node: { ...DECISION, id: "other", ref_id: "r-other", title: "Earlier answer" },
          depth: 2,
        },
      ],
    },
  }));
  const onSelect = vi.fn();
  render(<NodeInspector graph={GRAPH} nodeId={CHUNK.id} inspect={inspect} onSelect={onSelect} />);

  expect(screen.getByRole("heading", { name: CHUNK.title })).toBeInTheDocument();
  expect(screen.getByText("Refunds are issued within 14 days.")).toBeInTheDocument();
  expect(screen.getByText("Chunk 3 of the document")).toBeInTheDocument();
  expect(inspect).toHaveBeenCalledWith(CHUNK.id);

  const fed = await screen.findByText("Decisions it fed (2)");
  expect(fed).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Earlier answer" })).toHaveAttribute(
    "href",
    "/evidence/r-other",
  );

  fireEvent.click(screen.getByRole("button", { name: DOCUMENT.title }));
  expect(onSelect).toHaveBeenCalledWith(DOCUMENT.id);
});

test("falls back to the loaded graph when the API cannot be reached", async () => {
  const inspect = vi.fn(async (): Promise<NodeInspection> => ({ ok: false, error: "Down." }));
  render(<NodeInspector graph={GRAPH} nodeId={DECISION.id} inspect={inspect} onSelect={vi.fn()} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Down.");
  expect(screen.getByText("Evidence for this (2)")).toBeInTheDocument();
  expect(screen.getByText("Informed (1)")).toBeInTheDocument();
});

test("explains an edge with its full provenance", () => {
  const onSelect = vi.fn();
  render(<EdgeInspector graph={GRAPH} edgeId={CHUNKED.id} onSelect={onSelect} />);
  expect(
    screen.getByRole("heading", { name: "Refund Policy · Refunds derived from Refund Policy" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Chunk 3 of the document")).toBeInTheDocument();
  expect(screen.getByText("cortex.knowledge.citations")).toBeInTheDocument();
  expect(screen.getByRole("meter", { name: "Edge confidence" })).toHaveAttribute(
    "aria-valuenow",
    "100",
  );
  fireEvent.click(screen.getByRole("button", { name: "Refund Policy" }));
  expect(onSelect).toHaveBeenCalledWith(DOCUMENT.id);
});
