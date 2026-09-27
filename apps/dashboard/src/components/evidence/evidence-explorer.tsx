"use client";

import "@xyflow/react/dist/style.css";

import type { EvidenceGraph, EvidenceGraphNode } from "@celestra/cortex-sdk";
import {
  Background,
  Controls,
  type Dimensions,
  type Edge,
  Handle,
  MarkerType,
  MiniMap,
  type Node,
  type NodeChange,
  type NodeDimensionChange,
  type NodeProps,
  type OnSelectionChangeParams,
  Position,
  ReactFlow,
} from "@xyflow/react";
import { useCallback, useMemo, useState } from "react";

import {
  EdgeInspector,
  type InspectNode,
  NodeInspector,
} from "@/components/evidence/evidence-inspector";
import {
  confidenceOpacity,
  edgeVerb,
  formatConfidence,
  layoutEvidence,
  NODE_TYPE_LABELS,
} from "@/lib/evidence";

type Selection = { kind: "node"; id: string } | { kind: "edge"; id: string } | null;

type EvidenceFlowNode = Node<
  { node: EvidenceGraphNode; root: boolean; related: boolean },
  "evidence"
>;

function EvidenceNodeCard({ data, selected }: NodeProps<EvidenceFlowNode>) {
  const { node, root, related } = data;
  const percent = Math.round(node.confidence * 100);
  return (
    <div
      className={[
        "bg-card text-card-foreground w-60 rounded-md border px-3 py-2 shadow-xs transition-[opacity,box-shadow]",
        root ? "border-foreground border-2" : "",
        selected ? "ring-foreground ring-2 ring-offset-2 ring-offset-[var(--background)]" : "",
      ].join(" ")}
      style={{ opacity: related ? confidenceOpacity(node.confidence) : 0.25 }}
      title={`${NODE_TYPE_LABELS[node.type]}: ${node.title} (${formatConfidence(node.confidence)})`}
    >
      <Handle type="target" position={Position.Left} className="!bg-muted-foreground !size-1.5" />
      <div className="text-muted-foreground flex items-center justify-between text-[10px] tracking-wide uppercase">
        <span>{NODE_TYPE_LABELS[node.type]}</span>
        <span className="font-mono tabular-nums">{percent}%</span>
      </div>
      <p className="mt-1 line-clamp-2 text-xs leading-snug">{node.title}</p>
      <div className="bg-muted mt-2 h-1 overflow-hidden rounded-full">
        <div className="bg-foreground h-full" style={{ width: `${percent}%` }} />
      </div>
      <Handle type="source" position={Position.Right} className="!bg-muted-foreground !size-1.5" />
    </div>
  );
}

const nodeTypes = { evidence: EvidenceNodeCard };

/**
 * The decision's evidence as an interactive graph. Evidence sits to the left
 * of the root and flows right; stroke weight and opacity follow confidence.
 */
export function EvidenceExplorer({
  graph,
  inspect,
}: {
  graph: EvidenceGraph;
  inspect: InspectNode;
}) {
  const [selection, setSelection] = useState<Selection>({ kind: "node", id: graph.root_id });
  const [measured, setMeasured] = useState<Record<string, Dimensions>>({});

  // Nodes are derived from props, so measurements must be fed back for the minimap and fitView.
  const onNodesChange = useCallback((changes: NodeChange<EvidenceFlowNode>[]) => {
    const sized = changes.filter(
      (change): change is NodeDimensionChange & { dimensions: Dimensions } =>
        change.type === "dimensions" && change.dimensions !== undefined,
    );
    if (sized.length === 0) return;
    setMeasured((current) => {
      const next = { ...current };
      for (const change of sized) next[change.id] = change.dimensions;
      return next;
    });
  }, []);

  const related = useMemo(() => {
    if (selection?.kind !== "node") return null;
    const ids = new Set([selection.id]);
    for (const edge of graph.edges) {
      if (edge.from_node_id === selection.id) ids.add(edge.to_node_id);
      if (edge.to_node_id === selection.id) ids.add(edge.from_node_id);
    }
    return ids;
  }, [graph, selection]);

  const nodes = useMemo<EvidenceFlowNode[]>(
    () =>
      layoutEvidence(graph).map(({ node, x, y }) => ({
        id: node.id,
        type: "evidence",
        position: { x, y },
        data: { node, root: node.id === graph.root_id, related: related?.has(node.id) ?? true },
        selected: selection?.kind === "node" && selection.id === node.id,
        measured: measured[node.id],
        ariaLabel: `${NODE_TYPE_LABELS[node.type]}: ${node.title}`,
      })),
    [graph, measured, related, selection],
  );

  const edges = useMemo<Edge[]>(
    () =>
      graph.edges.map((edge) => {
        const confidence = edge.provenance.confidence;
        const selected = selection?.kind === "edge" && selection.id === edge.id;
        const touching =
          selection?.kind === "node" &&
          (edge.from_node_id === selection.id || edge.to_node_id === selection.id);
        const faded = selection?.kind === "node" && !touching;
        const stroke =
          edge.type === "contradicts" ? "var(--muted-foreground)" : "var(--foreground)";
        return {
          id: edge.id,
          source: edge.from_node_id,
          target: edge.to_node_id,
          selected,
          label: selected || touching ? edgeVerb(edge.type) : undefined,
          labelStyle: { fontSize: 10, fill: "var(--muted-foreground)" },
          labelBgStyle: { fill: "var(--background)" },
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: stroke },
          interactionWidth: 16,
          ariaLabel: `${edgeVerb(edge.type)}, ${formatConfidence(confidence)} confidence`,
          style: {
            stroke,
            strokeWidth: (selected ? 1.5 : 0.75) + 2.5 * confidence,
            strokeDasharray: edge.type === "contradicts" ? "6 4" : undefined,
            opacity: faded ? 0.15 : 0.35 + 0.65 * confidence,
          },
        };
      }),
    [graph, selection],
  );

  const selectNode = useCallback((id: string) => setSelection({ kind: "node", id }), []);
  const onSelectionChange = useCallback(({ nodes: picked }: OnSelectionChangeParams) => {
    const [only] = picked;
    if (picked.length === 1 && only) setSelection({ kind: "node", id: only.id });
  }, []);

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="bg-background h-[36rem] overflow-hidden border" data-testid="evidence-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeClick={(_, node) => selectNode(node.id)}
          onEdgeClick={(_, edge) => setSelection({ kind: "edge", id: edge.id })}
          onPaneClick={() => setSelection(null)}
          onSelectionChange={onSelectionChange}
          nodesConnectable={false}
          nodesDraggable={false}
          edgesFocusable
          fitView
          fitViewOptions={{ padding: 0.2, maxZoom: 1.1 }}
          minZoom={0.15}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} />
          <Controls showInteractive={false} />
          <MiniMap
            pannable
            zoomable
            nodeColor={(node) =>
              node.id === graph.root_id ? "var(--foreground)" : "var(--muted-foreground)"
            }
            nodeBorderRadius={4}
            className="!border"
          />
        </ReactFlow>
      </div>
      <aside
        aria-label="Inspector"
        aria-live="polite"
        className="max-h-[36rem] overflow-y-auto border p-4"
      >
        {selection?.kind === "node" ? (
          <NodeInspector
            key={selection.id}
            graph={graph}
            nodeId={selection.id}
            inspect={inspect}
            onSelect={selectNode}
          />
        ) : selection?.kind === "edge" ? (
          <EdgeInspector graph={graph} edgeId={selection.id} onSelect={selectNode} />
        ) : (
          <p className="text-muted-foreground text-sm">
            Select a node to inspect it, or an edge to see why the link exists.
          </p>
        )}
      </aside>
    </div>
  );
}
