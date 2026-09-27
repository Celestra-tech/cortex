"use client";

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatImpact, type ImpactDatum } from "@/lib/scenarios";

const TOOLTIP_STYLE = {
  background: "var(--card)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  color: "var(--foreground)",
  fontSize: 12,
};

const SERIES: Record<string, string> = {
  upside: "Upside",
  downside: "Downside",
  net: "Net expected impact",
};

export function ImpactChart({ data }: { data: readonly ImpactDatum[] }) {
  return (
    <figure className="flex flex-col gap-2">
      <div aria-hidden="true">
        <ComposedChart
          responsive
          data={[...data]}
          style={{ width: "100%", height: 280 }}
          margin={{ top: 8, right: 8, bottom: 0, left: -16 }}
          stackOffset="sign"
        >
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            axisLine={{ stroke: "var(--border)" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatImpact(v)}
          />
          <ReferenceLine y={0} stroke="var(--foreground)" strokeOpacity={0.4} />
          <Tooltip
            cursor={{ fill: "var(--accent)" }}
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, name) => [formatImpact(Number(value)), SERIES[String(name)] ?? name]}
          />
          <Legend
            formatter={(name: string) => SERIES[name] ?? name}
            wrapperStyle={{ fontSize: 12, color: "var(--muted-foreground)" }}
          />
          <Bar dataKey="upside" stackId="impact" fill="var(--foreground)" maxBarSize={48} />
          <Bar
            dataKey="downside"
            stackId="impact"
            fill="var(--muted-foreground)"
            fillOpacity={0.45}
            maxBarSize={48}
          />
          <Line
            dataKey="net"
            stroke="var(--foreground)"
            strokeWidth={0}
            legendType="circle"
            dot={{
              r: 5,
              fill: "var(--card)",
              stroke: "var(--foreground)",
              strokeWidth: 2,
              strokeOpacity: 1,
            }}
            activeDot={{ r: 6, fill: "var(--card)", stroke: "var(--foreground)", strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </div>
      <figcaption className="text-muted-foreground text-xs">
        Each bar splits a scenario&apos;s expected impact into the gain from meeting the objective
        and the harm from everything that can go wrong; the ring marks the net.
      </figcaption>
      <table className="sr-only">
        <caption>Expected impact by scenario</caption>
        <thead>
          <tr>
            <th scope="col">Scenario</th>
            <th scope="col">Upside</th>
            <th scope="col">Downside</th>
            <th scope="col">Net</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.id}>
              <th scope="row">{d.name}</th>
              <td>{formatImpact(d.upside)}</td>
              <td>{formatImpact(d.downside)}</td>
              <td>{formatImpact(d.net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
