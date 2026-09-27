"use client";

import type { Assumption, Scenario } from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";
import Link from "next/link";
import { useState } from "react";

import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import {
  ASSUMPTION_KIND_LABELS,
  formatImpact,
  formatPercent,
  formatShift,
  groupAssumptions,
  scenarioLabel,
} from "@/lib/scenarios";

function sourceLabel(source: string): string {
  if (source === "cortex.evidence") return "Evidence Graph";
  if (source === "cortex.scenario.planner") return "Scenario planner";
  if (source.startsWith("api:")) return "Your request";
  return source;
}

function AssumptionItem({
  assumption,
  decisionId,
}: {
  assumption: Assumption;
  decisionId: string;
}) {
  const stance = assumption.kind === "strategy";
  return (
    <li className="flex flex-col gap-2 py-3">
      <p className="text-sm">{assumption.statement}</p>
      {stance ? null : (
        <div className="text-muted-foreground flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <span className="inline-flex items-center gap-2">
            Recorded
            <ConfidenceMeter
              value={assumption.baseline_confidence}
              label="Recorded confidence"
              className="w-16"
            />
          </span>
          <span className="inline-flex items-center gap-2">
            Assumed
            <ConfidenceMeter
              value={assumption.confidence}
              label="Assumed confidence"
              className="w-16"
            />
          </span>
          <span className="tabular-nums">{formatShift(assumption)}</span>
        </div>
      )}
      <p className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
        <span title={assumption.source}>Source: {sourceLabel(assumption.source)}</span>
        {assumption.evidence_node_id ? (
          <Link
            href={`/evidence/${decisionId}`}
            className="underline underline-offset-4"
            title={`Evidence node ${assumption.evidence_node_id}`}
          >
            View in evidence graph
          </Link>
        ) : null}
      </p>
    </li>
  );
}

export function AssumptionsPanel({
  scenarios,
  decisionId,
  initialId,
}: {
  scenarios: readonly Scenario[];
  decisionId: string;
  initialId: string;
}) {
  const [selectedId, setSelectedId] = useState(initialId);
  const selected = scenarios.find((s) => s.id === selectedId) ?? scenarios[0];
  if (!selected) return null;
  const groups = groupAssumptions(selected.assumptions);
  const statements = new Map(selected.assumptions.map((a) => [a.id, a.statement]));
  const outcomes = [...selected.outcomes].sort(
    (a, b) => Math.abs(b.expected_impact) - Math.abs(a.expected_impact),
  );

  return (
    <div className="flex flex-col gap-5">
      <div role="group" aria-label="Scenario" className="flex flex-wrap gap-2">
        {scenarios.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-pressed={s.id === selected.id}
            onClick={() => setSelectedId(s.id)}
            className={`rounded-full border px-3 py-1 text-xs ${
              s.id === selected.id
                ? "bg-foreground text-background border-foreground"
                : "text-muted-foreground"
            }`}
          >
            {scenarioLabel(s.type)}
          </button>
        ))}
      </div>

      <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
        <section
          aria-label={`${scenarioLabel(selected.type)} assumptions`}
          className="flex flex-col gap-4"
        >
          {groups.map((group) => (
            <div key={group.kind}>
              <h3 className="flex items-center gap-2 text-xs font-medium">
                <Badge variant="secondary">{ASSUMPTION_KIND_LABELS[group.kind]}</Badge>
                <span className="text-muted-foreground">{group.items.length}</span>
              </h3>
              <ul className="divide-y">
                {group.items.map((assumption) => (
                  <AssumptionItem
                    key={assumption.id}
                    assumption={assumption}
                    decisionId={decisionId}
                  />
                ))}
              </ul>
            </div>
          ))}
        </section>

        <section
          aria-label={`${scenarioLabel(selected.type)} outcomes`}
          className="flex flex-col gap-2"
        >
          <h3 className="text-xs font-medium">Outcomes, largest expected effect first</h3>
          <ul className="divide-y border">
            {outcomes.map((outcome) => (
              <li key={outcome.id} className="flex flex-col gap-1 px-3 py-3 text-sm">
                <div className="flex items-baseline justify-between gap-3">
                  <span>{outcome.result}</span>
                  <span className="font-mono text-xs tabular-nums">
                    {formatImpact(outcome.expected_impact)}
                  </span>
                </div>
                <p className="text-muted-foreground text-xs tabular-nums">
                  Impact {formatImpact(outcome.impact)} × likelihood{" "}
                  {formatPercent(outcome.likelihood)}
                  {outcome.assumption_id && statements.has(outcome.assumption_id)
                    ? ` · driven by “${statements.get(outcome.assumption_id)}”`
                    : ""}
                </p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
