import type {
  DecisionScenariosResponse,
  Scenario,
  Simulation,
  SimulationCreate,
  SimulationListResponse,
  SimulationSummary,
} from "@celestra/cortex-types";

import type { RequestOptions } from "./http";
import { APIResource, type PageParams, segment } from "./resource";
import {
  decisionScenariosSchema,
  scenarioSchema,
  simulationCreateSchema,
  simulationListSchema,
  simulationSchema,
  validateInput,
} from "./types";

/**
 * The Scenario Simulator: plausible ways to act on a recorded decision, each
 * scored on evidence quality, uncertainty, constraints, objective alignment,
 * and risk exposure, with every assumption stated.
 *
 * This is planning, not forecasting: scenarios differ by the stance they take
 * toward the same evidence, and the stance is returned with each one.
 */
export class Scenarios extends APIResource {
  /**
   * Simulates scenarios for a decision (best, base, worst case, aggressive,
   * conservative) and ranks them. Not retried: each call is a new simulation.
   */
  async simulate(params: SimulationCreate, options?: RequestOptions): Promise<Simulation> {
    validateInput("scenarios.simulate", simulationCreateSchema, params);
    return this.transport.request({
      operation: "scenarios.simulate",
      method: "POST",
      path: "/v2/scenarios",
      json: params,
      schema: simulationSchema,
      options: { maxRetries: 0, ...options },
    });
  }

  /** One scenario with its assumptions and outcomes. */
  get(scenarioId: string, options?: RequestOptions): Promise<Scenario> {
    return this.transport.request({
      operation: "scenarios.get",
      path: `/v2/scenarios/${segment(scenarioId)}`,
      schema: scenarioSchema,
      options,
    });
  }

  /** Simulations across the organization, newest first, each with its recommended scenario. */
  list(params: PageParams = {}, options?: RequestOptions): Promise<SimulationListResponse> {
    return this.transport.request({
      operation: "scenarios.list",
      path: "/v2/scenarios",
      query: { limit: params.limit, offset: params.offset },
      schema: simulationListSchema,
      options,
    });
  }

  /** Every simulation, fetching pages lazily. */
  iter(params: PageParams = {}, options?: RequestOptions): AsyncGenerator<SimulationSummary> {
    return this.paginate((page) => this.list(page, options), params);
  }

  /** A decision's simulations, newest first, with every scenario. Defaults to the latest 5. */
  forDecision(
    decisionId: string,
    params: PageParams = {},
    options?: RequestOptions,
  ): Promise<DecisionScenariosResponse> {
    return this.transport.request({
      operation: "scenarios.forDecision",
      path: `/v2/decisions/${segment(decisionId)}/scenarios`,
      query: { limit: params.limit, offset: params.offset },
      schema: decisionScenariosSchema,
      options,
    });
  }
}
