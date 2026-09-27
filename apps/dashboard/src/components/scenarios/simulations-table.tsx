import type { SimulationListResponse } from "@celestra/cortex-sdk";
import Link from "next/link";

import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { formatTimestamp } from "@/lib/evidence";
import { scenarioLabel } from "@/lib/scenarios";

export function SimulationsTable({ page }: { page: SimulationListResponse }) {
  if (page.items.length === 0) {
    return (
      <p className="text-muted-foreground border px-4 py-12 text-center text-sm">
        No simulations yet. Open a decision in{" "}
        <Link href="/evidence" className="text-foreground underline underline-offset-4">
          Evidence
        </Link>{" "}
        and choose Plan scenarios to explore the ways it could play out.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto border">
      <table className="w-full text-sm">
        <caption className="sr-only">Simulations, newest first</caption>
        <thead className="text-muted-foreground text-left text-xs">
          <tr className="border-b">
            <th scope="col" className="px-4 py-3 font-medium">
              Time (UTC)
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Decision and objective
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Recommended
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              Score
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Confidence
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {page.items.map((item) => (
            <tr key={item.simulation_id}>
              <td className="px-4 py-3 font-mono text-xs whitespace-nowrap tabular-nums">
                {formatTimestamp(item.created_at)}
              </td>
              <td className="max-w-md px-4 py-3">
                <Link
                  href={`/scenarios/${item.decision_id}?simulation=${item.simulation_id}`}
                  className="block truncate underline-offset-4 hover:underline"
                  title={item.decision_title}
                >
                  {item.decision_title}
                </Link>
                {item.objective !== item.decision_title ? (
                  <span
                    className="text-muted-foreground block truncate text-xs"
                    title={item.objective}
                  >
                    {item.objective}
                  </span>
                ) : null}
              </td>
              <td className="px-4 py-3 whitespace-nowrap">
                {scenarioLabel(item.recommended.type)}
                <span className="text-muted-foreground text-xs"> of {item.scenario_count}</span>
              </td>
              <td className="px-4 py-3 text-right font-mono text-xs tabular-nums">
                {item.recommended.score.toFixed(2)}
              </td>
              <td className="px-4 py-3">
                <ConfidenceMeter value={item.recommended.confidence} className="w-20" />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
