import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@celestra/cortex-ui/components/card";

import { StatusIndicator } from "@/components/status-indicator";
import type { Connection } from "@/lib/system-status";

function formatLatency(latencyMs: number | null): string {
  if (latencyMs === null) return "—";
  return latencyMs < 10 ? `${latencyMs.toFixed(1)} ms` : `${Math.round(latencyMs)} ms`;
}

export function ConnectionCard({ connection }: { connection: Connection }) {
  return (
    <Card
      role="article"
      aria-label={`${connection.name} connection`}
      data-state={connection.state}
      className="gap-8"
    >
      <CardHeader className="flex flex-col gap-3">
        <div className="flex w-full items-center justify-between gap-3">
          <CardTitle className="text-lg">{connection.name}</CardTitle>
          <StatusIndicator state={connection.state} />
        </div>
        <CardDescription>{connection.description}</CardDescription>
      </CardHeader>
      <CardContent className="mt-auto">
        <dl className="divide-y border-t text-sm">
          <div className="flex items-baseline justify-between gap-4 py-3">
            <dt className="text-muted-foreground text-xs">Endpoint</dt>
            <dd className="min-w-0 truncate font-mono text-[13px]">{connection.endpoint}</dd>
          </div>
          <div className="flex items-baseline justify-between gap-4 pt-3">
            <dt className="text-muted-foreground text-xs">Latency</dt>
            <dd className="font-mono text-[13px] tabular-nums">
              {formatLatency(connection.latencyMs)}
            </dd>
          </div>
        </dl>
      </CardContent>
    </Card>
  );
}
