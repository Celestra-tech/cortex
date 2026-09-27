/**
 * Mirrors `cortex_api.schemas.evidence`. Keep both sides in sync.
 *
 * Edges point upstream to downstream: from the evidence to what it informed.
 * `supports` and `contradicts` read forward ("memory supports decision"); the
 * others read backward ("chunk derived from document").
 */
import type { Id, Timestamp } from "./memory";

export type EvidenceNodeType =
  | "decision"
  | "memory"
  | "message"
  | "conversation"
  | "document"
  | "chunk"
  | "knowledge"
  | "benchmark";

export type EvidenceEdgeType =
  "supports" | "references" | "derived_from" | "retrieved_from" | "generated_by" | "contradicts";

export interface EvidenceNode {
  id: Id;
  type: EvidenceNodeType;
  /** The record this node stands for: for completion decisions, the completion id. */
  ref_id: Id | null;
  title: string;
  /** 0..1. For decisions, how well the direct evidence backs it. */
  confidence: number;
  created_at: Timestamp;
  /** When the underlying record came to be. */
  occurred_at: Timestamp;
  metadata: Record<string, unknown>;
}

export interface Provenance {
  confidence: number;
  explanation: string;
  /** The component (`cortex.*`) or API key that asserted the link. */
  source: string;
  /** When the relationship was observed. */
  timestamp: Timestamp;
}

export interface EvidenceEdge {
  id: Id;
  type: EvidenceEdgeType;
  /** Upstream: the evidence. */
  from_node_id: Id;
  /** Downstream: what the evidence informed. */
  to_node_id: Id;
  provenance: Provenance;
  created_at: Timestamp;
}

export interface SupportingEvidence {
  node: EvidenceNode;
  /** Hops to the decision along the strongest route. */
  depth: number;
  /** Edge and intermediate-node confidences multiplied along that route. */
  path_confidence: number;
  /** `path_confidence` times the node's own confidence. */
  strength: number;
  /** Edge ids of the route, from this node to the decision. */
  path: Id[];
}

export interface Contradiction {
  node: EvidenceNode;
  edge: EvidenceEdge;
}

export interface DecisionEvidence {
  decision: EvidenceNode;
  /** Strongest first. */
  supporting: SupportingEvidence[];
  contradicting: Contradiction[];
  counts: Partial<Record<EvidenceNodeType, number>>;
}

export interface DecisionListResponse {
  items: EvidenceNode[];
  total: number;
  limit: number;
  offset: number;
}

export interface EvidenceGraphNode extends EvidenceNode {
  /** Signed hops from the root: negative upstream (evidence), positive downstream. */
  depth: number;
}

export interface EvidenceTimelineEvent {
  at: Timestamp;
  kind: "node" | "edge";
  /** The node or edge id. */
  id: Id;
  label: string;
  source: string | null;
  confidence: number;
}

export interface EvidenceGraph {
  root_id: Id;
  depth: number;
  nodes: EvidenceGraphNode[];
  edges: EvidenceEdge[];
  /** Records and links in time order. */
  timeline: EvidenceTimelineEvent[];
  /** True when the server's edge budget cut the graph short. */
  truncated: boolean;
}

export interface EvidenceNeighbor {
  edge: EvidenceEdge;
  node: EvidenceNode;
}

export interface EvidenceNodeDetail {
  node: EvidenceNode;
  upstream: EvidenceNeighbor[];
  downstream: EvidenceNeighbor[];
  decisions: { node: EvidenceNode; depth: number }[];
}

export interface EvidencePath {
  source_id: Id;
  target_id: Id;
  connected: boolean;
  edges: EvidenceEdge[];
  nodes: EvidenceNode[];
}

export interface EvidenceInput {
  type: EvidenceNodeType;
  /** Evidence with the same type and ref_id is shared across decisions. */
  ref_id?: Id | null;
  title: string;
  /** How reliable the evidence is. Defaults to 1. */
  confidence?: number;
  metadata?: Record<string, unknown>;
  /** Defaults to `supports`. */
  relation?: EvidenceEdgeType;
  /** How strongly the evidence bears on the decision. Defaults to 1. */
  relation_confidence?: number;
  explanation: string;
  observed_at?: Timestamp | null;
}

export interface DecisionCreate {
  /** Your id for the decision; generated when omitted. */
  ref_id?: Id | null;
  title: string;
  /** Omit to derive it from the evidence. */
  confidence?: number | null;
  metadata?: Record<string, unknown>;
  /** Who asserts the links, e.g. `policy-engine@2`. */
  source?: string | null;
  evidence: EvidenceInput[];
}
