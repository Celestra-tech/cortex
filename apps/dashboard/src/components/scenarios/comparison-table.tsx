import type { Scenario } from "@celestra/cortex-sdk";

import { comparisonRows, scenarioLabel } from "@/lib/scenarios";

export function ComparisonTable({ scenarios }: { scenarios: readonly Scenario[] }) {
  const rows = comparisonRows(scenarios);
  return (
    <div className="overflow-x-auto border">
      <table className="w-full text-sm">
        <caption className="sr-only">
          Scenarios compared on every criterion; the best value in each row is bold.
        </caption>
        <thead className="text-muted-foreground text-left text-xs">
          <tr className="border-b">
            <th scope="col" className="px-4 py-3 font-medium">
              Criterion
            </th>
            {scenarios.map((s) => (
              <th
                key={s.id}
                scope="col"
                className="px-4 py-3 text-right font-medium whitespace-nowrap"
              >
                {scenarioLabel(s.type)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row, index) => (
            <tr
              key={row.key}
              className={index === 4 ? "border-t-foreground/30 border-t-2" : undefined}
            >
              <th scope="row" className="px-4 py-3 text-left font-normal">
                <span className="block">{row.label}</span>
                <span className="text-muted-foreground block text-xs">{row.help}</span>
              </th>
              {row.values.map((cell) => {
                const best = row.best.includes(cell.id);
                return (
                  <td
                    key={cell.id}
                    className={`px-4 py-3 text-right font-mono text-xs tabular-nums ${
                      best ? "font-semibold" : "text-muted-foreground"
                    }`}
                  >
                    {cell.formatted}
                    {best ? <span className="sr-only"> (best)</span> : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
