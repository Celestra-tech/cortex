import type {
  EvidenceEdge,
  EvidenceEdgeType,
  EvidenceGraph,
  EvidenceGraphNode,
  EvidenceNodeType,
} from "@celestra/cortex-sdk";

const AT = "2026-09-27T12:00:00+00:00";

export function graphNode(
  id: string,
  type: EvidenceNodeType,
  title: string,
  overrides: Partial<EvidenceGraphNode> = {},
): EvidenceGraphNode {
  return {
    id,
    type,
    ref_id: `ref-${id}`,
    title,
    confidence: 1,
    created_at: AT,
    occurred_at: AT,
    metadata: {},
    depth: 0,
    ...overrides,
  };
}

export function graphEdge(
  id: string,
  from: string,
  to: string,
  type: EvidenceEdgeType,
  explanation: string,
  confidence = 1,
): EvidenceEdge {
  return {
    id,
    type,
    from_node_id: from,
    to_node_id: to,
    provenance: { confidence, explanation, source: "cortex.knowledge.citations", timestamp: AT },
    created_at: AT,
  };
}

export const DECISION = graphNode(
  "00000000-0000-7000-8000-000000000001",
  "decision",
  "Answer: refunds?",
  {
    confidence: 0.82,
    metadata: { kind: "completion", provider: "openai", model: "gpt-4.1" },
  },
);
export const CHUNK = graphNode(
  "00000000-0000-7000-8000-000000000002",
  "chunk",
  "Refund Policy · Refunds",
  {
    depth: -1,
    confidence: 0.9,
    metadata: { excerpt: "Refunds are issued within 14 days.", section: "Refunds" },
  },
);
export const DOCUMENT = graphNode(
  "00000000-0000-7000-8000-000000000003",
  "document",
  "Refund Policy",
  {
    depth: -2,
  },
);
export const MEMORY = graphNode("00000000-0000-7000-8000-000000000004", "memory", "Prefers email", {
  depth: -1,
  confidence: 0.4,
});
export const REPLY = graphNode(
  "00000000-0000-7000-8000-000000000005",
  "message",
  "Assistant: 14 days",
  {
    depth: 1,
  },
);

export const CITED = graphEdge("e-cited", CHUNK.id, DECISION.id, "supports", "Cited as [1]", 0.9);
export const CHUNKED = graphEdge(
  "e-chunk",
  DOCUMENT.id,
  CHUNK.id,
  "derived_from",
  "Chunk 3 of the document",
);
export const RECALLED = graphEdge(
  "e-memory",
  MEMORY.id,
  DECISION.id,
  "contradicts",
  "Asked for phone",
  0.5,
);
export const REPLIED = graphEdge("e-reply", DECISION.id, REPLY.id, "generated_by", "Stored reply");

export const GRAPH: EvidenceGraph = {
  root_id: DECISION.id,
  depth: 4,
  nodes: [DECISION, CHUNK, DOCUMENT, MEMORY, REPLY],
  edges: [CITED, CHUNKED, RECALLED, REPLIED],
  timeline: [
    {
      at: AT,
      kind: "node",
      id: DOCUMENT.id,
      label: "Document: Refund Policy",
      source: null,
      confidence: 1,
    },
    {
      at: "2026-09-27T12:00:01+00:00",
      kind: "edge",
      id: CITED.id,
      label: "Refund Policy · Refunds supports Answer: refunds?",
      source: "cortex.knowledge.citations",
      confidence: 0.9,
    },
  ],
  truncated: false,
};
