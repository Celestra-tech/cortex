import {
  AuthenticationError,
  type DecisionEvidence,
  type EvidenceGraph,
  NotFoundError,
  ValidationError,
} from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";
import { Button } from "@celestra/cortex-ui/components/button";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { inspectNode } from "@/app/evidence/actions";
import { AppHeader } from "@/components/app-header";
import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { EvidenceExplorer } from "@/components/evidence/evidence-explorer";
import { ContradictionList, SupportingEvidenceList } from "@/components/evidence/evidence-lists";
import { ProvenanceTimeline } from "@/components/evidence/provenance-timeline";
import { cortexFor, requireSession } from "@/lib/auth";
import { clampDepth, formatTimestamp, MAX_DEPTH, NODE_TYPE_LABELS } from "@/lib/evidence";

export const metadata: Metadata = { title: "Decision evidence" };

type Params = Promise<{ decisionId: string }>;

export default async function DecisionEvidencePage({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Promise<{ depth?: string }>;
}) {
  const { decisionId } = await params;
  const depth = clampDepth((await searchParams).depth);
  const self = `/evidence/${encodeURIComponent(decisionId)}`;
  const session = await requireSession(self);

  const cortex = cortexFor(session);
  let evidence: DecisionEvidence;
  let graph: EvidenceGraph;
  try {
    [evidence, graph] = await Promise.all([
      cortex.evidence.decision(decisionId, { depth }),
      cortex.evidence.graph(decisionId, { depth }),
    ]);
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ValidationError) notFound();
    if (error instanceof AuthenticationError) redirect(`/logout?next=${encodeURIComponent(self)}`);
    throw error;
  }
  const { decision } = evidence;
  const counts = Object.entries(evidence.counts).filter(([, count]) => count);

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader session={session} current="/evidence" />

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-10 px-6 pb-24 sm:px-10">
        <div className="pt-10">
          <Link href="/evidence" className="text-muted-foreground text-xs">
            ← All decisions
          </Link>
          <h1 className="mt-3 text-2xl font-semibold tracking-[-0.02em] break-words">
            {decision.title}
          </h1>
          <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <ConfidenceMeter
              value={decision.confidence}
              label="Decision confidence"
              className="w-40"
            />
            <time dateTime={decision.occurred_at} className="text-xs tabular-nums">
              {formatTimestamp(decision.occurred_at)} UTC
            </time>
            <span className="font-mono text-xs" title="Decision id">
              {decision.ref_id}
            </span>
            <Button asChild variant="outline" size="sm">
              <Link href={`/scenarios/${encodeURIComponent(decisionId)}`}>Plan scenarios</Link>
            </Button>
          </div>
          {counts.length > 0 ? (
            <ul className="mt-4 flex flex-wrap gap-2" aria-label="Evidence by type">
              {counts.map(([type, count]) => (
                <li key={type}>
                  <Badge variant="secondary">
                    {count} {NODE_TYPE_LABELS[type as keyof typeof NODE_TYPE_LABELS] ?? type}
                  </Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <section aria-labelledby="graph-heading" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 id="graph-heading" className="text-sm font-medium">
                Evidence graph
              </h2>
              <p className="text-muted-foreground mt-1 text-xs">
                Evidence flows left to right into the decision. Heavier, darker links carry more
                confidence; dashed links contradict.
              </p>
            </div>
            <form method="get" className="flex items-end gap-2 text-sm" aria-label="Graph depth">
              <label className="flex flex-col gap-1">
                <span className="text-muted-foreground text-xs">Depth</span>
                <select
                  name="depth"
                  defaultValue={String(depth)}
                  className="border-input bg-background h-9 rounded-md border px-2"
                >
                  {Array.from({ length: MAX_DEPTH }, (_, i) => i + 1).map((d) => (
                    <option key={d} value={d}>
                      {d} {d === 1 ? "hop" : "hops"}
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit" variant="outline" size="sm">
                Apply
              </Button>
            </form>
          </div>
          {graph.truncated ? (
            <p
              role="status"
              className="text-muted-foreground border border-dashed px-3 py-2 text-xs"
            >
              This graph is larger than the server&apos;s edge budget, so only the nearest links are
              shown. Lower the depth to see a complete neighborhood.
            </p>
          ) : null}
          <EvidenceExplorer graph={graph} inspect={inspectNode} />
        </section>

        <div className="grid gap-10 lg:grid-cols-2">
          <section aria-labelledby="supporting-heading" className="flex flex-col gap-3">
            <h2 id="supporting-heading" className="text-sm font-medium">
              Supporting evidence ({evidence.supporting.length})
            </h2>
            <SupportingEvidenceList items={evidence.supporting} />
          </section>
          <section aria-labelledby="contradicting-heading" className="flex flex-col gap-3">
            <h2 id="contradicting-heading" className="text-sm font-medium">
              Contradicting evidence ({evidence.contradicting.length})
            </h2>
            <ContradictionList items={evidence.contradicting} />
          </section>
        </div>

        <section aria-labelledby="timeline-heading" className="flex flex-col gap-3">
          <h2 id="timeline-heading" className="text-sm font-medium">
            Provenance timeline
          </h2>
          <ProvenanceTimeline events={graph.timeline} />
        </section>
      </main>
    </div>
  );
}
