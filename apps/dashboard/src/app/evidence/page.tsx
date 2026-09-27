import { AuthenticationError, type DecisionListResponse } from "@celestra/cortex-sdk";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { DecisionsTable } from "@/components/evidence/decisions-table";
import { cortexFor, requireSession } from "@/lib/auth";

export const metadata: Metadata = { title: "Evidence" };

const PAGE_SIZE = 50;

function pageHref(page: number): string {
  return page > 1 ? `/evidence?page=${page}` : "/evidence";
}

export default async function EvidencePage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string }>;
}) {
  const params = await searchParams;
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const session = await requireSession(pageHref(page));

  let decisions: DecisionListResponse;
  try {
    decisions = await cortexFor(session).evidence.listDecisions({
      limit: PAGE_SIZE,
      offset: (page - 1) * PAGE_SIZE,
    });
  } catch (error) {
    if (error instanceof AuthenticationError) redirect("/logout?next=/evidence");
    throw error;
  }
  const pages = Math.max(1, Math.ceil(decisions.total / PAGE_SIZE));

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader session={session} current="/evidence" />

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-6 pb-24 sm:px-10">
        <div className="flex flex-wrap items-end justify-between gap-4 pt-10">
          <div>
            <h1 className="text-3xl font-semibold tracking-[-0.03em]">Evidence</h1>
            <p className="text-muted-foreground mt-2 text-sm">
              Every decision, traceable to the memories, messages, documents, and retrievals that
              produced it.
            </p>
          </div>
          <span className="text-muted-foreground text-xs tabular-nums">
            {decisions.total.toLocaleString("en-US")} decisions
          </span>
        </div>

        <DecisionsTable page={decisions} />

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
