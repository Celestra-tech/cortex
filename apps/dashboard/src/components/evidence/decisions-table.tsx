import type { DecisionListResponse } from "@celestra/cortex-sdk";
import Link from "next/link";

import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { formatTimestamp } from "@/lib/evidence";

function origin(metadata: Record<string, unknown>): string {
  if (metadata.kind === "external") return "Recorded via API";
  const model = typeof metadata.model === "string" ? metadata.model : null;
  const provider = typeof metadata.provider === "string" ? metadata.provider : null;
  return model ? `${provider ? `${provider}/` : ""}${model}` : "Completion";
}

export function DecisionsTable({ page }: { page: DecisionListResponse }) {
  if (page.items.length === 0) {
    return (
      <p className="text-muted-foreground border px-4 py-12 text-center text-sm">
        No decisions yet. Every chat completion is recorded here with the evidence behind it.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto border">
      <table className="w-full text-sm">
        <caption className="sr-only">Decisions, newest first</caption>
        <thead className="text-muted-foreground text-left text-xs">
          <tr className="border-b">
            <th scope="col" className="px-4 py-3 font-medium">
              Time (UTC)
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Decision
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Origin
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Confidence
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {page.items.map((decision) => (
            <tr key={decision.id}>
              <td className="px-4 py-3 font-mono text-xs whitespace-nowrap tabular-nums">
                {formatTimestamp(decision.occurred_at)}
              </td>
              <td className="max-w-md px-4 py-3">
                {decision.ref_id ? (
                  <Link
                    href={`/evidence/${decision.ref_id}`}
                    className="block truncate underline-offset-4 hover:underline"
                    title={decision.title}
                  >
                    {decision.title}
                  </Link>
                ) : (
                  decision.title
                )}
              </td>
              <td className="text-muted-foreground px-4 py-3 whitespace-nowrap">
                {origin(decision.metadata)}
              </td>
              <td className="px-4 py-3">
                <ConfidenceMeter value={decision.confidence} className="w-20" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
