"use client";

import {
  CartesianGrid,
  LabelList,
  ReferenceArea,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";

import { type ConfidenceDatum, formatPercent } from "@/lib/scenarios";

const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 11 };

function PointTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: readonly { payload?: ConfidenceDatum }[];
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <div className="bg-card rounded-md border px-3 py-2 text-xs shadow-sm">
      <p className="font-medium">
        {point.name}
        {point.recommended ? " · recommended" : ""}
      </p>
      <p className="text-muted-foreground mt-1 tabular-nums">
        Score {point.score.toFixed(2)} · confidence {formatPercent(point.confidence)} · success{" "}
        {formatPercent(point.success)}
      </p>
    </div>
  );
}

/** Score against confidence: the top-right corner is strong and well-founded. */
export function ConfidenceChart({ data }: { data: readonly ConfidenceDatum[] }) {
  const recommended = data.filter((d) => d.recommended);
  const others = data.filter((d) => !d.recommended);
  return (
    <figure className="flex flex-col gap-2">
      <div aria-hidden="true">
        <ScatterChart
          responsive
          style={{ width: "100%", height: 280 }}
          margin={{ top: 20, right: 16, bottom: 8, left: -16 }}
        >
          <CartesianGrid stroke="var(--border)" />
          <ReferenceArea x1={0.5} x2={1} y1={0.5} y2={1} fill="var(--accent)" fillOpacity={0.6} />
          <XAxis
            type="number"
            dataKey="score"
            name="Score"
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tick={AXIS_TICK}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
            label={{ value: "Score", position: "insideBottomRight", offset: -4, ...AXIS_TICK }}
          />
          <YAxis
            type="number"
            dataKey="confidence"
            name="Confidence"
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tick={AXIS_TICK}
            tickFormatter={(v: number) => formatPercent(v)}
            axisLine={false}
            tickLine={false}
          />
          <ZAxis type="number" dataKey="success" range={[60, 420]} domain={[0, 1]} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<PointTooltip />} />
          <Scatter
            data={others}
            fill="var(--muted-foreground)"
            fillOpacity={0.55}
            isAnimationActive={false}
          >
            <LabelList dataKey="name" position="top" fill="var(--muted-foreground)" fontSize={11} />
          </Scatter>
          <Scatter data={recommended} fill="var(--foreground)" isAnimationActive={false}>
            <LabelList dataKey="name" position="top" fill="var(--foreground)" fontSize={11} />
          </Scatter>
        </ScatterChart>
      </div>
      <figcaption className="text-muted-foreground text-xs">
        Higher and further right is better. Bubble size is the chance of success; the shaded corner
        holds scenarios that score well and rest on assumptions worth trusting.
      </figcaption>
      <table className="sr-only">
        <caption>Score, confidence, and success likelihood by scenario</caption>
        <thead>
          <tr>
            <th scope="col">Scenario</th>
            <th scope="col">Score</th>
            <th scope="col">Confidence</th>
            <th scope="col">Success likelihood</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.id}>
              <th scope="row">
                {d.name}
                {d.recommended ? " (recommended)" : ""}
              </th>
              <td>{d.score.toFixed(2)}</td>
              <td>{formatPercent(d.confidence)}</td>
              <td>{formatPercent(d.success)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
