"use client";

import type { EvidenceEdge, EvidenceGraph, EvidenceNode } from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import type { NodeInspection } from "@/app/evidence/actions";
import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import {
  describeEdge,
  displayMetadata,
  edgeVerb,
  formatConfidence,
  formatTimestamp,
  NODE_TYPE_LABELS,
  neighborsIn,
} from "@/lib/evidence";

export type InspectNode = (nodeId: string) => Promise<NodeInspection>;

function Provenance({ edge }: { edge: EvidenceEdge }) {
  return (
    <p className="text-muted-foreground mt-1 text-xs">
      <span className="font-mono">{edge.provenance.source}</span>
      {" · "}
      <span className="tabular-nums">{formatConfidence(edge.provenance.confidence)}</span>
      {" · "}
      <time dateTime={edge.provenance.timestamp} className="tabular-nums">
        {formatTimestamp(edge.provenance.timestamp)}
      </time>
    </p>
  );
}

function NeighborRow({
  edge,
  node,
  inGraph,
  onSelect,
}: {
  edge: EvidenceEdge;
  node: EvidenceNode;
  inGraph: boolean;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <li className="py-2.5">
      <p className="text-muted-foreground text-[11px] tracking-wide uppercase">
        {edgeVerb(edge.type)} · {NODE_TYPE_LABELS[node.type]}
      </p>
      {inGraph ? (
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className="block max-w-full truncate text-left text-sm underline-offset-4 hover:underline"
          title={node.title}
        >
          {node.title}
        </button>
      ) : (
        <p className="truncate text-sm" title={node.title}>
          {node.title}
        </p>
      )}
      <p className="mt-0.5 text-xs">{edge.provenance.explanation}</p>
      <Provenance edge={edge} />
    </li>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-t pt-3">
      <h3 className="text-muted-foreground text-xs font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** Details of one node: what it is, what backs it, what it informed, and which decisions it fed. */
export function NodeInspector({
  graph,
  nodeId,
  inspect,
  onSelect,
}: {
  graph: EvidenceGraph;
  nodeId: string;
  inspect: InspectNode;
  onSelect: (nodeId: string) => void;
}) {
  const node = graph.nodes.find((candidate) => candidate.id === nodeId);
  const local = useMemo(() => neighborsIn(graph, nodeId), [graph, nodeId]);
  const inGraph = useMemo(() => new Set(graph.nodes.map((n) => n.id)), [graph]);
  const [loaded, setLoaded] = useState<{ id: string; result: NodeInspection } | null>(null);

  useEffect(() => {
    let current = true;
    inspect(nodeId)
      .catch((): NodeInspection => ({ ok: false, error: "The Cortex API is unavailable." }))
      .then((result) => {
        if (current) setLoaded({ id: nodeId, result });
      });
    return () => {
      current = false;
    };
  }, [inspect, nodeId]);

  if (!node) return null;
  const result = loaded?.id === nodeId ? loaded.result : null;
  const detail = result?.ok ? result.detail : null;
  const upstream = detail?.upstream ?? local.upstream;
  const downstream = detail?.downstream ?? local.downstream;
  const metadata = displayMetadata(node.metadata);
  const excerpt = typeof node.metadata.excerpt === "string" ? node.metadata.excerpt : null;

  return (
    <div className="flex flex-col gap-4" aria-busy={result === null}>
      <div>
        <div className="flex items-center gap-2">
          <Badge variant={node.type === "decision" ? "default" : "outline"}>
            {NODE_TYPE_LABELS[node.type]}
          </Badge>
          {node.id === graph.root_id ? (
            <span className="text-muted-foreground text-xs">Root</span>
          ) : null}
        </div>
        <h2 className="mt-2 text-base leading-snug font-medium break-words">{node.title}</h2>
        <div className="mt-2">
          <ConfidenceMeter value={node.confidence} className="w-32" />
        </div>
        <p className="text-muted-foreground mt-2 text-xs">
          <time dateTime={node.occurred_at}>{formatTimestamp(node.occurred_at)} UTC</time>
          {node.ref_id ? (
            <>
              {" · "}
              <span className="font-mono" title="Source record id">
                {node.ref_id}
              </span>
            </>
          ) : null}
        </p>
      </div>

      {excerpt ? (
        <blockquote className="text-muted-foreground border-l-2 pl-3 text-xs leading-relaxed">
          {excerpt}
        </blockquote>
      ) : null}

      {metadata.filter(([key]) => key !== "excerpt").length > 0 ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
          {metadata
            .filter(([key]) => key !== "excerpt")
            .map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="truncate font-mono" title={value}>
                  {value}
                </dd>
              </div>
            ))}
        </dl>
      ) : null}

      {result && !result.ok ? (
        <p role="alert" className="text-muted-foreground border border-dashed px-3 py-2 text-xs">
          {result.error} Showing links from the loaded graph only.
        </p>
      ) : null}

      <Section title={`Evidence for this (${upstream.length})`}>
        {upstream.length === 0 ? (
          <p className="text-muted-foreground py-2 text-xs">
            An original record: nothing upstream.
          </p>
        ) : (
          <ul className="divide-y">
            {upstream.map(({ edge, node: neighbor }) => (
              <NeighborRow
                key={edge.id}
                edge={edge}
                node={neighbor}
                inGraph={inGraph.has(neighbor.id)}
                onSelect={onSelect}
              />
            ))}
          </ul>
        )}
      </Section>

      <Section title={`Informed (${downstream.length})`}>
        {downstream.length === 0 ? (
          <p className="text-muted-foreground py-2 text-xs">Nothing downstream.</p>
        ) : (
          <ul className="divide-y">
            {downstream.map(({ edge, node: neighbor }) => (
              <NeighborRow
                key={edge.id}
                edge={edge}
                node={neighbor}
                inGraph={inGraph.has(neighbor.id)}
                onSelect={onSelect}
              />
            ))}
          </ul>
        )}
      </Section>

      {detail && detail.decisions.length > 0 ? (
        <Section title={`Decisions it fed (${detail.decisions.length})`}>
          <ul className="divide-y">
            {detail.decisions.map(({ node: decision, depth }) => (
              <li key={decision.id} className="flex items-center justify-between gap-3 py-2">
                {decision.ref_id && decision.id !== graph.root_id ? (
                  <Link
                    href={`/evidence/${decision.ref_id}`}
                    className="truncate text-sm underline-offset-4 hover:underline"
                    title={decision.title}
                  >
                    {decision.title}
                  </Link>
                ) : (
                  <span className="truncate text-sm" title={decision.title}>
                    {decision.title}
                  </span>
                )}
                <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                  {depth === 1 ? "direct" : `${depth} hops`}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

/** One edge's provenance: who asserted the link, when, how confidently, and why. */
export function EdgeInspector({
  graph,
  edgeId,
  onSelect,
}: {
  graph: EvidenceGraph;
  edgeId: string;
  onSelect: (nodeId: string) => void;
}) {
  const edge = graph.edges.find((candidate) => candidate.id === edgeId);
  const from = graph.nodes.find((n) => n.id === edge?.from_node_id);
  const to = graph.nodes.find((n) => n.id === edge?.to_node_id);
  if (!edge || !from || !to) return null;
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Badge variant={edge.type === "contradicts" ? "muted" : "outline"}>
          {edgeVerb(edge.type)}
        </Badge>
        <h2 className="mt-2 text-base leading-snug font-medium break-words">
          {describeEdge(edge.type, from.title, to.title)}
        </h2>
      </div>
      <div>
        <h3 className="text-muted-foreground text-xs font-medium">Explanation</h3>
        <p className="mt-1 text-sm">{edge.provenance.explanation}</p>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-2 text-xs">
        <dt className="text-muted-foreground">Confidence</dt>
        <dd>
          <ConfidenceMeter value={edge.provenance.confidence} label="Edge confidence" />
        </dd>
        <dt className="text-muted-foreground">Source</dt>
        <dd className="font-mono break-all">{edge.provenance.source}</dd>
        <dt className="text-muted-foreground">Observed</dt>
        <dd>
          <time dateTime={edge.provenance.timestamp} className="tabular-nums">
            {formatTimestamp(edge.provenance.timestamp)} UTC
          </time>
        </dd>
      </dl>
      <div className="flex flex-col gap-1 border-t pt-3 text-sm">
        {[
          { label: "Evidence", node: from },
          { label: "Informed", node: to },
        ].map(({ label, node }) => (
          <p key={label} className="flex items-baseline gap-2">
            <span className="text-muted-foreground w-16 shrink-0 text-xs">{label}</span>
            <button
              type="button"
              onClick={() => onSelect(node.id)}
              className="truncate text-left underline-offset-4 hover:underline"
              title={node.title}
            >
              {node.title}
            </button>
          </p>
        ))}
      </div>
    </div>
  );
}
