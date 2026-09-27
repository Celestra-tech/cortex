"use server";

import {
  AuthenticationError,
  CortexError,
  NotFoundError,
  ValidationError,
} from "@celestra/cortex-sdk";
import { redirect } from "next/navigation";

import { cortexFor, getSession } from "@/lib/auth";
import { parseSimulationForm } from "@/lib/scenarios";

export interface SimulationFormState {
  error: string | null;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Simulates scenarios for a decision as the signed-in organization, then shows the result. */
export async function runSimulation(
  decisionId: string,
  _previous: SimulationFormState,
  form: FormData,
): Promise<SimulationFormState> {
  if (typeof decisionId !== "string" || !UUID_PATTERN.test(decisionId)) {
    return { error: "Not a decision id." };
  }
  const session = await getSession();
  if (!session) return { error: "Your session has expired. Sign in again." };

  const parsed = parseSimulationForm(form);
  if (!parsed.ok) return { error: parsed.error };

  let simulationId: string;
  try {
    const simulation = await cortexFor(session).scenarios.simulate({
      decision_id: decisionId,
      ...parsed.input,
    });
    simulationId = simulation.simulation_id;
  } catch (error) {
    if (error instanceof NotFoundError) return { error: "This decision no longer exists." };
    if (error instanceof ValidationError) {
      return { error: `Cortex rejected the request: ${error.message}` };
    }
    if (error instanceof AuthenticationError) {
      return { error: "Your API key was revoked. Sign in again." };
    }
    if (error instanceof CortexError) return { error: "The Cortex API is unavailable." };
    throw error;
  }
  redirect(`/scenarios/${decisionId}?simulation=${simulationId}`);
}
