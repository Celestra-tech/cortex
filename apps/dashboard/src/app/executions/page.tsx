import {
  AuthenticationError,
  type ExecutionListResponse,
  type OverviewResponse,
} from "@celestra/cortex-sdk";
import { Button } from "@celestra/cortex-ui/components/button";
import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { ExecutionStats, ExecutionsTable } from "@/components/executions-table";
import { cortexFor, requireSession } from "@/lib/auth";

export const metadata: Metadata = { title: "Executions" };

const PAGE_SIZE = 50;
const PROVIDERS = ["openai", "anthropic", "gemini"] as const;

interface SearchParams {
  provider?: string;
  status?: string;
  page?: string;
}

function pageHref(params: SearchParams, page: number): string {
  const query = new URLSearchParams();
  if (params.provider) query.set("provider", params.provider);
  if (params.status) query.set("status", params.status);
  if (page > 1) query.set("page", String(page));
  const search = query.toString();
  return search ? `/executions?${search}` : "/executions";
}

export default async function ExecutionsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const params = await searchParams;
  const session = await requireSession(pageHref(params, 1));
  const page = Math.max(1, Number.parseInt(params.page ?? "1", 10) || 1);
  const provider = PROVIDERS.find((p) => p === params.provider);
  const success =
    params.status === "failed" ? false : params.status === "succeeded" ? true : undefined;

  const cortex = cortexFor(session);
  let executions: ExecutionListResponse;
  let overview: OverviewResponse | null;
  try {
    [executions, overview] = await Promise.all([
      cortex.router.listExecutions({
        provider,
        success,
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      }),
      cortex.observatory.overview({ windowHours: 24 }).catch(() => null),
    ]);
  } catch (error) {
    if (error instanceof AuthenticationError) redirect("/logout?next=/executions");
    throw error;
  }
  const pages = Math.max(1, Math.ceil(executions.total / PAGE_SIZE));

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader session={session} current="/executions" />

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-8 px-6 pb-24 sm:px-10">
        <div className="pt-10">
          <h1 className="text-3xl font-semibold tracking-[-0.03em]">Executions</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            Every provider call, including retries and fallbacks. Newest first.
          </p>
        </div>

        {overview ? <ExecutionStats stats={overview.requests} windowHours={24} /> : null}

        <form
          method="get"
          className="flex flex-wrap items-end gap-3 text-sm"
          aria-label="Filter executions"
        >
          <label className="flex flex-col gap-1">
            <span className="text-muted-foreground text-xs">Provider</span>
            <select
              name="provider"
              defaultValue={provider ?? ""}
              className="border-input bg-background h-9 rounded-md border px-2"
            >
              <option value="">All</option>
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-muted-foreground text-xs">Outcome</span>
            <select
              name="status"
              defaultValue={params.status ?? ""}
              className="border-input bg-background h-9 rounded-md border px-2"
            >
              <option value="">All</option>
              <option value="succeeded">Succeeded</option>
              <option value="failed">Failed</option>
            </select>
          </label>
          <Button type="submit" variant="outline" size="sm">
            Apply
          </Button>
          <span className="text-muted-foreground ml-auto text-xs tabular-nums">
            {executions.total.toLocaleString("en-US")} total
          </span>
        </form>

        <ExecutionsTable page={executions} />

        {pages > 1 ? (
          <nav aria-label="Pagination" className="flex items-center justify-between text-sm">
            {page > 1 ? <Link href={pageHref(params, page - 1)}>← Newer</Link> : <span />}
            <span className="text-muted-foreground tabular-nums">
              Page {page} of {pages}
            </span>
            {page < pages ? <Link href={pageHref(params, page + 1)}>Older →</Link> : <span />}
          </nav>
        ) : null}
      </main>
    </div>
  );
}
