import {
  AuthenticationError,
  type DecisionEvidence,
  type DecisionScenariosResponse,
  NotFoundError,
  type Simulation,
  ValidationError,
} from "@celestra/cortex-sdk";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppHeader } from "@/components/app-header";
import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { AssumptionsPanel } from "@/components/scenarios/assumptions-panel";
import { ComparisonTable } from "@/components/scenarios/comparison-table";
import { ConfidenceChart } from "@/components/scenarios/confidence-chart";
import { ImpactChart } from "@/components/scenarios/impact-chart";
import { ScenarioCards } from "@/components/scenarios/scenario-cards";
import { SimulationForm } from "@/components/scenarios/simulation-form";
import { cortexFor, requireSession } from "@/lib/auth";
import { formatTimestamp } from "@/lib/evidence";
import {
  CRITERIA,
  confidenceData,
  formatPercent,
  impactData,
  scenarioLabel,
} from "@/lib/scenarios";

export const metadata: Metadata = { title: "Scenarios" };

const HISTORY = 20;

type Params = Promise<{ decisionId: string }>;

function Parameters({ simulation }: { simulation: Simulation }) {
  const p = simulation.parameters;
  const weights = (p.weights ?? {}) as Record<string, number>;
  const constraints = Array.isArray(p.constraints) ? p.constraints.length : 0;
  const stated = Array.isArray(p.assumptions) ? p.assumptions.length : 0;
  const tolerance = typeof p.risk_tolerance === "number" ? p.risk_tolerance : null;
  return (
    <dl className="text-muted-foreground grid gap-x-8 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
      <div>
        <dt>Risk tolerance</dt>
        <dd className="text-foreground font-mono tabular-nums">
          {tolerance === null ? "—" : formatPercent(tolerance)}
        </dd>
      </div>
      <div>
        <dt>Constraints · stated assumptions</dt>
        <dd className="text-foreground font-mono tabular-nums">
          {constraints} · {stated}
        </dd>
      </div>
      <div className="sm:col-span-2">
        <dt>Criterion weights</dt>
        <dd className="text-foreground flex flex-wrap gap-x-3 font-mono tabular-nums">
          {CRITERIA.map(({ key, label }) => (
            <span key={key} title={label}>
              {label.split(" ")[0]} {formatPercent(weights[key] ?? 0)}
            </span>
          ))}
        </dd>
      </div>
    </dl>
  );
}

export default async function DecisionScenariosPage({
  params,
  searchParams,
}: {
  params: Params;
  searchParams: Promise<{ simulation?: string }>;
}) {
  const { decisionId } = await params;
  const { simulation: requested } = await searchParams;
  const self = `/scenarios/${encodeURIComponent(decisionId)}`;
  const session = await requireSession(self);

  const cortex = cortexFor(session);
  let evidence: DecisionEvidence;
  let history: DecisionScenariosResponse;
  try {
    [evidence, history] = await Promise.all([
      cortex.evidence.decision(decisionId, { depth: 1 }),
      cortex.scenarios.forDecision(decisionId, { limit: HISTORY }),
    ]);
  } catch (error) {
    if (error instanceof NotFoundError || error instanceof ValidationError) notFound();
    if (error instanceof AuthenticationError) redirect(`/logout?next=${encodeURIComponent(self)}`);
    throw error;
  }
  const { decision } = evidence;
  const simulation = requested
    ? history.simulations.find((s) => s.simulation_id === requested)
    : history.simulations[0];
  const recommended = simulation?.scenarios.find((s) => s.id === simulation.recommended_id);

  return (
    <div className="flex min-h-dvh flex-col">
      <AppHeader session={session} current="/scenarios" />

      <main className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-10 px-6 pb-24 sm:px-10">
        <div className="pt-10">
          <Link href="/scenarios" className="text-muted-foreground text-xs">
            ← All simulations
          </Link>
          <h1 className="mt-3 text-2xl font-semibold tracking-[-0.02em] break-words">
            {decision.title}
          </h1>
          <div className="text-muted-foreground mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
            <ConfidenceMeter
              value={decision.confidence}
              label="Decision confidence"
              className="w-40"
            />
            <Link href={`/evidence/${decisionId}`} className="text-xs underline underline-offset-4">
              Evidence behind this decision
            </Link>
          </div>
        </div>

        <details open={history.total === 0} className="group border px-5 py-4">
          <summary className="cursor-pointer text-sm font-medium">
            {history.total === 0 ? "Plan scenarios" : "Run a new simulation"}
          </summary>
          <div className="pt-5">
            <SimulationForm decisionId={decisionId} decisionTitle={decision.title} />
          </div>
        </details>

        {history.simulations.length > 1 ? (
          <nav aria-label="Simulations" className="flex flex-col gap-2">
            <h2 className="text-muted-foreground text-xs">
              Previous simulations ({history.total.toLocaleString("en-US")})
            </h2>
            <ul className="flex flex-wrap gap-2 text-xs">
              {history.simulations.map((s) => {
                const current = s.simulation_id === simulation?.simulation_id;
                return (
                  <li key={s.simulation_id}>
                    <Link
                      href={`${self}?simulation=${s.simulation_id}`}
                      aria-current={current ? "page" : undefined}
                      className={`block rounded-full border px-3 py-1 tabular-nums ${
                        current
                          ? "bg-foreground text-background border-foreground"
                          : "text-muted-foreground"
                      }`}
                    >
                      {formatTimestamp(s.created_at)}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </nav>
        ) : null}

        {requested && !simulation ? (
          <p role="status" className="text-muted-foreground border border-dashed px-3 py-2 text-sm">
            That simulation isn&apos;t among this decision&apos;s latest {HISTORY}.{" "}
            <Link href={self} className="text-foreground underline underline-offset-4">
              Show the latest
            </Link>
          </p>
        ) : null}

        {simulation && recommended ? (
          <>
            <section aria-labelledby="summary-heading" className="flex flex-col gap-4">
              <div>
                <h2 id="summary-heading" className="text-sm font-medium">
                  Recommended: {scenarioLabel(recommended.type)}
                </h2>
                <p className="text-muted-foreground mt-1 text-sm">
                  Objective: {simulation.objective}
                </p>
                <p className="text-muted-foreground mt-1 text-xs">
                  Simulated {formatTimestamp(simulation.created_at)} UTC. Scores weigh the criteria
                  below; every scenario states the stance it takes toward the same evidence.
                </p>
              </div>
              <Parameters simulation={simulation} />
              <ScenarioCards
                scenarios={simulation.scenarios}
                recommendedId={simulation.recommended_id}
              />
            </section>

            <div className="grid gap-10 lg:grid-cols-2">
              <section aria-labelledby="impact-heading" className="flex flex-col gap-3">
                <h2 id="impact-heading" className="text-sm font-medium">
                  Expected impact
                </h2>
                <ImpactChart data={impactData(simulation.scenarios)} />
              </section>
              <section aria-labelledby="confidence-heading" className="flex flex-col gap-3">
                <h2 id="confidence-heading" className="text-sm font-medium">
                  Score and confidence
                </h2>
                <ConfidenceChart
                  data={confidenceData(simulation.scenarios, simulation.recommended_id)}
                />
              </section>
            </div>

            <section aria-labelledby="comparison-heading" className="flex flex-col gap-3">
              <h2 id="comparison-heading" className="text-sm font-medium">
                Comparison
              </h2>
              <ComparisonTable scenarios={simulation.scenarios} />
            </section>

            <section aria-labelledby="assumptions-heading" className="flex flex-col gap-3">
              <div>
                <h2 id="assumptions-heading" className="text-sm font-medium">
                  Assumptions and outcomes
                </h2>
                <p className="text-muted-foreground mt-1 text-xs">
                  What each scenario takes to be true, how far that departs from the recorded
                  evidence, and what follows from it.
                </p>
              </div>
              <AssumptionsPanel
                key={simulation.simulation_id}
                scenarios={simulation.scenarios}
                decisionId={decisionId}
                initialId={simulation.recommended_id}
              />
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}
