import type { ExecutionListResponse, ModelExecution, RequestStats } from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";

export interface ExecutionFilters {
  provider?: string;
  success?: boolean;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

function formatCost(usd: number): string {
  if (usd === 0) return "$0";
  return usd < 0.01 ? `$${usd.toFixed(5)}` : `$${usd.toFixed(3)}`;
}

function formatPercent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function formatMs(value: number | null): string {
  return value === null ? "—" : `${Math.round(value)} ms`;
}

export function ExecutionStats({
  stats,
  windowHours,
}: {
  stats: RequestStats;
  windowHours: number;
}) {
  const items = [
    { label: "Requests", value: stats.total.toLocaleString("en-US") },
    { label: "Success rate", value: formatPercent(stats.success_rate) },
    { label: "p95 latency", value: formatMs(stats.p95_latency_ms) },
    { label: "Fallback rate", value: formatPercent(stats.fallback_rate) },
    { label: "Tokens", value: stats.tokens.toLocaleString("en-US") },
    { label: "Est. cost", value: formatCost(stats.cost_estimate) },
  ];
  return (
    <section
      aria-label={`Last ${windowHours} hours`}
      className="grid grid-cols-2 gap-px border sm:grid-cols-3 lg:grid-cols-6"
    >
      {items.map((item) => (
        <div key={item.label} className="bg-background p-4">
          <p className="text-muted-foreground text-xs">{item.label}</p>
          <p className="mt-1 font-mono text-lg tabular-nums">{item.value}</p>
        </div>
      ))}
    </section>
  );
}

function Outcome({ execution }: { execution: ModelExecution }) {
  if (execution.success) {
    return <Badge variant="outline">{execution.is_fallback ? "Fallback" : "Success"}</Badge>;
  }
  return (
    <Badge variant="muted" title={execution.error ?? undefined}>
      {execution.error_type ?? "Failed"}
    </Badge>
  );
}

export function ExecutionsTable({ page }: { page: ExecutionListResponse }) {
  if (page.items.length === 0) {
    return (
      <p className="text-muted-foreground border px-4 py-12 text-center text-sm">
        No executions yet. Send a chat completion and it will appear here.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto border">
      <table className="w-full text-sm">
        <caption className="sr-only">Model executions, newest first</caption>
        <thead className="text-muted-foreground text-left text-xs">
          <tr className="border-b">
            <th scope="col" className="px-4 py-3 font-medium">
              Time (UTC)
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Model
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Outcome
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              Latency
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              Tokens
            </th>
            <th scope="col" className="px-4 py-3 text-right font-medium">
              Cost
            </th>
            <th scope="col" className="px-4 py-3 font-medium">
              Completion
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {page.items.map((execution) => (
            <tr key={execution.id} data-success={execution.success}>
              <td className="px-4 py-3 font-mono text-xs whitespace-nowrap tabular-nums">
                {formatTime(execution.created_at)}
              </td>
              <td className="px-4 py-3 whitespace-nowrap">
                <span className="text-muted-foreground">{execution.provider}/</span>
                {execution.model}
                {execution.attempt > 1 ? (
                  <span className="text-muted-foreground ml-2 text-xs">
                    attempt {execution.attempt}
                  </span>
                ) : null}
              </td>
              <td className="px-4 py-3">
                <Outcome execution={execution} />
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">
                {Math.round(execution.latency_ms)} ms
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">
                {(execution.prompt_tokens + execution.completion_tokens).toLocaleString("en-US")}
              </td>
              <td className="px-4 py-3 text-right font-mono tabular-nums">
                {formatCost(execution.cost_estimate)}
              </td>
              <td
                className="text-muted-foreground px-4 py-3 font-mono text-xs"
                title={execution.completion_id}
              >
                {execution.completion_id.slice(-12)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
