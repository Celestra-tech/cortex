import type { Contradiction, SupportingEvidence } from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";

import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { NODE_TYPE_LABELS } from "@/lib/evidence";

export function SupportingEvidenceList({ items }: { items: SupportingEvidence[] }) {
  if (items.length === 0) {
    return (
      <p className="text-muted-foreground border px-4 py-8 text-center text-sm">
        Nothing supports this decision yet, so its confidence is 0%.
      </p>
    );
  }
  return (
    <ol className="divide-y border" aria-label="Supporting evidence, strongest first">
      {items.map((item) => (
        <li key={item.node.id} className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="min-w-0">
            <p className="truncate text-sm" title={item.node.title}>
              {item.node.title}
            </p>
            <p className="text-muted-foreground mt-0.5 text-xs">
              {NODE_TYPE_LABELS[item.node.type]} ·{" "}
              {item.depth === 1 ? "direct" : `${item.depth} hops away`}
            </p>
          </div>
          <ConfidenceMeter value={item.strength} label={`Strength of ${item.node.title}`} />
        </li>
      ))}
    </ol>
  );
}

export function ContradictionList({ items }: { items: Contradiction[] }) {
  if (items.length === 0) {
    return (
      <p className="text-muted-foreground border px-4 py-8 text-center text-sm">
        No contradicting evidence.
      </p>
    );
  }
  return (
    <ul className="divide-y border border-dashed" aria-label="Contradicting evidence">
      {items.map(({ node, edge }) => (
        <li key={edge.id} className="px-4 py-3">
          <div className="flex items-center justify-between gap-4">
            <p className="truncate text-sm" title={node.title}>
              {node.title}
            </p>
            <Badge variant="muted">{NODE_TYPE_LABELS[node.type]}</Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-xs">{edge.provenance.explanation}</p>
        </li>
      ))}
    </ul>
  );
}
