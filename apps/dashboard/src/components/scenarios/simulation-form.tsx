"use client";

import { Button } from "@celestra/cortex-ui/components/button";
import { useActionState, useId, useState } from "react";

import { runSimulation, type SimulationFormState } from "@/app/scenarios/actions";
import { MAX_ROWS, SCENARIO_LABELS, SCENARIO_TYPES } from "@/lib/scenarios";

const INPUT = "border-input bg-background h-9 rounded-md border px-3 text-sm";

function useRows() {
  const [rows, setRows] = useState<number[]>([]);
  const [next, setNext] = useState(0);
  return {
    rows,
    add: () => {
      setRows((current) => (current.length < MAX_ROWS ? [...current, next] : current));
      setNext((n) => n + 1);
    },
    remove: (key: number) => setRows((current) => current.filter((k) => k !== key)),
  };
}

export function SimulationForm({
  decisionId,
  decisionTitle,
}: {
  decisionId: string;
  decisionTitle: string;
}) {
  const [state, action, pending] = useActionState<SimulationFormState, FormData>(
    runSimulation.bind(null, decisionId),
    { error: null },
  );
  const constraints = useRows();
  const assumptions = useRows();
  const [tolerance, setTolerance] = useState(50);
  const id = useId();

  return (
    <form action={action} className="flex flex-col gap-6" aria-describedby={`${id}-status`}>
      <label className="flex flex-col gap-1.5">
        <span className="text-sm font-medium">Objective</span>
        <span className="text-muted-foreground text-xs">
          What acting on this decision should achieve. Leave blank to use the decision itself.
        </span>
        <textarea
          name="objective"
          rows={2}
          maxLength={1000}
          placeholder={decisionTitle}
          className="border-input bg-background rounded-md border px-3 py-2 text-sm"
        />
      </label>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">Constraints</legend>
        <p className="text-muted-foreground text-xs">
          Limits the plan must respect. Hard constraints have to hold; soft ones are costly to
          break.
        </p>
        {constraints.rows.map((key, index) => (
          <div key={key} className="flex flex-wrap gap-2">
            <input
              name="constraint"
              aria-label={`Constraint ${index + 1}`}
              maxLength={500}
              placeholder="e.g. Refunds stay under $500"
              className={`${INPUT} min-w-0 flex-1`}
            />
            <select
              name="severity"
              aria-label={`Constraint ${index + 1} severity`}
              className={INPUT}
            >
              <option value="soft">Soft</option>
              <option value="hard">Hard</option>
            </select>
            <Button type="button" variant="ghost" size="sm" onClick={() => constraints.remove(key)}>
              Remove
            </Button>
          </div>
        ))}
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={constraints.add}
            disabled={constraints.rows.length >= MAX_ROWS}
          >
            Add constraint
          </Button>
        </div>
      </fieldset>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">Assumptions</legend>
        <p className="text-muted-foreground text-xs">
          Conditions the objective depends on that the evidence doesn&apos;t cover, with how likely
          you think each one is.
        </p>
        {assumptions.rows.map((key, index) => (
          <div key={key} className="flex flex-wrap gap-2">
            <input
              name="assumption"
              aria-label={`Assumption ${index + 1}`}
              maxLength={500}
              placeholder="e.g. Finance approves the budget"
              className={`${INPUT} min-w-0 flex-1`}
            />
            <label className="text-muted-foreground flex items-center gap-1 text-xs">
              <input
                name="likelihood"
                type="number"
                min={0}
                max={100}
                defaultValue={70}
                aria-label={`Assumption ${index + 1} likelihood (%)`}
                className={`${INPUT} w-20 text-right`}
              />
              %
            </label>
            <Button type="button" variant="ghost" size="sm" onClick={() => assumptions.remove(key)}>
              Remove
            </Button>
          </div>
        ))}
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={assumptions.add}
            disabled={assumptions.rows.length >= MAX_ROWS}
          >
            Add assumption
          </Button>
        </div>
      </fieldset>

      <div className="grid gap-6 md:grid-cols-2">
        <label className="flex flex-col gap-1.5">
          <span className="flex items-baseline justify-between text-sm font-medium">
            Risk tolerance
            <output htmlFor={`${id}-tolerance`} className="font-mono text-xs tabular-nums">
              {tolerance}%
            </output>
          </span>
          <input
            id={`${id}-tolerance`}
            name="risk_tolerance"
            type="range"
            min={0}
            max={100}
            step={5}
            value={tolerance}
            onChange={(e) => setTolerance(Number(e.target.value))}
            className="accent-foreground"
          />
          <span className="text-muted-foreground flex justify-between text-xs">
            <span>Avoid harm</span>
            <span>Reach the objective</span>
          </span>
        </label>

        <fieldset className="flex flex-col gap-1.5">
          <legend className="text-sm font-medium">Scenarios</legend>
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-2 text-sm">
            {SCENARIO_TYPES.map((type) => (
              <label key={type} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  name="type"
                  value={type}
                  defaultChecked
                  className="accent-foreground"
                />
                {SCENARIO_LABELS[type]}
              </label>
            ))}
          </div>
        </fieldset>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <Button type="submit" disabled={pending}>
          {pending ? "Simulating…" : "Run simulation"}
        </Button>
        <p id={`${id}-status`} role="status" aria-live="polite" className="text-sm">
          {state.error}
        </p>
      </div>
    </form>
  );
}
