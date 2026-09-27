import { Button } from "@celestra/cortex-ui/components/button";
import Link from "next/link";

import { ConnectionCard } from "@/components/connection-card";
import { DASHBOARD_VERSION } from "@/lib/config";
import type { SystemStatus } from "@/lib/system-status";

function summarize(status: SystemStatus): string {
  const total = status.connections.length;
  const up = status.connections.filter((c) => c.state === "operational").length;
  if (up === total) return "All systems operational";
  if (up === 0) return "No services reachable";
  return `${up} of ${total} services operational`;
}

function formatCheckedAt(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

export function Dashboard({ status, apiDocsUrl }: { status: SystemStatus; apiDocsUrl: string }) {
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-6 sm:px-10">
        <span className="text-[13px] font-semibold tracking-[0.2em]">CELESTRA</span>
        <nav className="flex items-center gap-6 text-sm">
          <Link href="/executions">Executions</Link>
          <span className="text-muted-foreground font-mono text-xs">v{DASHBOARD_VERSION}</span>
        </nav>
      </header>

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-6 sm:px-10">
        <section className="pt-20 pb-20 sm:pt-32 sm:pb-24">
          <h1 className="text-5xl font-semibold tracking-[-0.035em] sm:text-7xl">
            CELESTRA Cortex
          </h1>
          <p className="text-muted-foreground mt-5 font-serif text-2xl italic sm:text-3xl">
            The Intelligence Infrastructure
          </p>
        </section>

        <section aria-labelledby="connections-heading" className="pb-24">
          <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 id="connections-heading" className="text-sm font-medium">
                Connections
              </h2>
              <p className="text-muted-foreground mt-1 text-sm">{summarize(status)}</p>
            </div>
            <p className="text-muted-foreground text-xs">
              Checked{" "}
              <time dateTime={status.checkedAt} className="font-mono tabular-nums">
                {formatCheckedAt(status.checkedAt)} UTC
              </time>
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            {status.connections.map((connection) => (
              <ConnectionCard key={connection.id} connection={connection} />
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="text-muted-foreground mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-6 text-xs sm:px-10">
          <span>API {status.apiVersion ? `v${status.apiVersion}` : "unavailable"}</span>
          <Button asChild variant="link" size="sm" className="text-muted-foreground h-auto px-0">
            <a href={apiDocsUrl} target="_blank" rel="noreferrer">
              API reference →
            </a>
          </Button>
        </div>
      </footer>
    </div>
  );
}
