import type { Scenario } from "@celestra/cortex-sdk";
import { Badge } from "@celestra/cortex-ui/components/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@celestra/cortex-ui/components/card";

import { ConfidenceMeter } from "@/components/evidence/confidence-meter";
import { formatImpact, scenarioLabel } from "@/lib/scenarios";

function evidenceSummary(evidence: Scenario["evidence"]): string {
  const parts = [`${evidence.relied} evidence ${evidence.relied === 1 ? "item" : "items"}`];
  if (evidence.excluded) parts.push(`${evidence.excluded} set aside`);
  if (evidence.contradictions) {
    parts.push(
      `${evidence.contradictions} ${evidence.contradictions === 1 ? "contradiction" : "contradictions"}`,
    );
  }
  return parts.join(" · ");
}

export function ScenarioCard({
  scenario,
  recommended,
}: {
  scenario: Scenario;
  recommended: boolean;
}) {
  const label = scenarioLabel(scenario.type);
  return (
    <Card
      aria-labelledby={`scenario-${scenario.id}`}
      role="article"
      className={recommended ? "border-foreground gap-4 py-5" : "gap-4 py-5"}
    >
      <CardHeader className="px-5">
        <div className="flex h-5 items-center justify-between gap-2">
          <span className="text-muted-foreground font-mono text-xs whitespace-nowrap tabular-nums">
            Rank {scenario.rank}
          </span>
          {recommended ? <Badge>Recommended</Badge> : null}
        </div>
        <CardTitle id={`scenario-${scenario.id}`} className="text-base">
          {label}
        </CardTitle>
        <CardDescription className="text-xs leading-relaxed">
          {scenario.description}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-5 text-sm">
        <div className="flex items-baseline justify-between">
          <span className="text-muted-foreground text-xs">Score</span>
          <span className="font-mono text-2xl font-semibold tabular-nums">
            {scenario.score.toFixed(2)}
          </span>
        </div>
        <dl className="flex flex-col gap-2 text-xs">
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">Confidence</dt>
            <dd>
              <ConfidenceMeter
                value={scenario.confidence}
                label={`${label} confidence`}
                className="w-20"
              />
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">Success likelihood</dt>
            <dd>
              <ConfidenceMeter
                value={scenario.success_likelihood}
                label={`${label} success likelihood`}
                className="w-20"
              />
            </dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="text-muted-foreground">Expected impact</dt>
            <dd className="font-mono tabular-nums">{formatImpact(scenario.expected_impact)}</dd>
          </div>
        </dl>
        <p className="text-muted-foreground border-t pt-3 text-xs">
          {evidenceSummary(scenario.evidence)}
          {scenario.evidence.truncated ? " (strongest shown)" : ""}
        </p>
      </CardContent>
    </Card>
  );
}

export function ScenarioCards({
  scenarios,
  recommendedId,
}: {
  scenarios: readonly Scenario[];
  recommendedId: string;
}) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(12.5rem,1fr))] gap-4">
      {scenarios.map((scenario) => (
        <ScenarioCard
          key={scenario.id}
          scenario={scenario}
          recommended={scenario.id === recommendedId}
        />
      ))}
    </div>
  );
}
