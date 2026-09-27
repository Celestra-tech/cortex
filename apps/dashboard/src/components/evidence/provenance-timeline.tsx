import type { EvidenceTimelineEvent } from "@celestra/cortex-sdk";

import { formatConfidence, formatTimestamp } from "@/lib/evidence";

/** Every record and link in the graph, in the order it came to be. */
export function ProvenanceTimeline({ events }: { events: EvidenceTimelineEvent[] }) {
  if (events.length === 0) {
    return <p className="text-muted-foreground text-sm">No provenance recorded.</p>;
  }
  return (
    <ol className="relative ml-2 border-l" aria-label="Provenance timeline">
      {events.map((event) => (
        <li
          key={`${event.kind}-${event.id}`}
          data-kind={event.kind}
          className="relative py-2.5 pl-6"
        >
          <span
            aria-hidden
            className={
              event.kind === "node"
                ? "bg-foreground absolute top-4 -left-[4.5px] size-2 rounded-full"
                : "bg-background absolute top-4 -left-[4.5px] size-2 rounded-full border"
            }
          />
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <time
              dateTime={event.at}
              className="text-muted-foreground font-mono text-xs tabular-nums"
            >
              {formatTimestamp(event.at)}
            </time>
            <span className="text-sm">{event.label}</span>
          </div>
          <p className="text-muted-foreground mt-0.5 text-xs">
            {event.kind === "node" ? "Recorded" : "Linked"}
            {event.source ? (
              <>
                {" by "}
                <span className="font-mono">{event.source}</span>
              </>
            ) : null}
            {" · "}
            <span className="tabular-nums">{formatConfidence(event.confidence)}</span> confidence
          </p>
        </li>
      ))}
    </ol>
  );
}
