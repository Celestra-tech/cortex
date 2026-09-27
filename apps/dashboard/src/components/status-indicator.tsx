import { Badge } from "@celestra/cortex-ui/components/badge";
import { cn } from "@celestra/cortex-ui/lib/utils";

import type { ConnectionState } from "@/lib/system-status";

const LABELS: Record<ConnectionState, string> = {
  operational: "Operational",
  offline: "Offline",
  unknown: "Unknown",
};

export function StatusIndicator({ state }: { state: ConnectionState }) {
  return (
    <Badge variant={state === "unknown" ? "muted" : "outline"} className="gap-2 py-1 pr-3 pl-2.5">
      <span
        aria-hidden
        className={cn(
          "size-1.5 rounded-full",
          state === "operational" && "bg-foreground",
          state === "offline" && "ring-foreground ring-1 ring-inset",
          state === "unknown" && "bg-muted-foreground/40",
        )}
      />
      {LABELS[state]}
    </Badge>
  );
}
