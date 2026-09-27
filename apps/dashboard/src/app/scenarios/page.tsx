import { AuthenticationError, type SimulationListResponse } from "@celestra/cortex-sdk";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { SimulationsTable } from "@/components/scenarios/simulations-table";
import { cortexFor, requireSession } from "@/lib/auth";

export const metadata: Metadata = { title: "Scenarios" };

const PAGE_SIZE = 50;

function pageHref(page: number): string {
  return page > 1 ? `/scenarios?page=${page}` : "/scenarios";
}

export default async function ScenariosPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const session = await requireSession(pageHref(page));

  let simulations: SimulationListResponse;
  try {
    simulations = await cortexFor(session).scenarios.list({
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    });
  } catch (error) {
    if (error instanceof AuthenticationError) redirect("/logout?next=/scenarios");
    throw error;
  }
  const pages = Math.max(1, Math.ceil(simulations.total / PAGE_SIZE));

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader session={session} current="/scenarios" />

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-6 pb-24 sm:px-10">
        <div className="flex flex-wrap items-end justify-between gap-4 pt-10">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em]">Scenario Center</h1>
            <p className="text-muted-foreground mt-2 max-w-2xl text-sm">
              Plausible ways to act on a decision, each with its stance, assumptions, and outcomes
              stated. These are plans to compare, not predictions.
            </p>
          </div>
          <span className="text-muted-foreground text-xs tabular-nums">
            {simulations.total.toLocaleString("en-US")} simulations
          </span>
        </div>

        <SimulationsTable page={simulations} />

        {pages > 1 ? (
          <nav aria-label="Pagination" className="flex items-center justify-between text-sm">
            {page > 1 ? <Link href={pageHref(page - 1)}>← Newer</Link> : <span />}
            <span className="text-muted-foreground tabular-nums">
              Page {page} of {pages}
            </span>
            {page < pages ? <Link href={pageHref(page + 1)}>Older →</Link> : <span />}
          </nav>
        ) : null}
      </main>
    </div>
  );
}
