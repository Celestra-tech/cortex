from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from typing import Any

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from ._validation import validate_params
from .models import (
    ConstraintParams,
    DecisionScenarios,
    Page,
    Scenario,
    ScenarioType,
    ScenarioWeightParams,
    Simulation,
    SimulationParams,
    SimulationSummary,
    StatedAssumptionParams,
)

SimulationPage = Page[SimulationSummary]


def _simulate(
    decision_id: str,
    objective: str | None,
    constraints: Sequence[ConstraintParams] | None,
    assumptions: Sequence[StatedAssumptionParams] | None,
    risk_tolerance: float | None,
    weights: ScenarioWeightParams | Mapping[str, float] | None,
    types: Sequence[ScenarioType] | None,
    depth: int | None,
    options: RequestOptions | None,
) -> Call:
    op = "scenarios.simulate"
    params: dict[str, Any] = {"decision_id": decision_id}
    for key, value in (
        ("objective", objective),
        ("constraints", None if constraints is None else list(constraints)),
        ("assumptions", None if assumptions is None else list(assumptions)),
        ("risk_tolerance", risk_tolerance),
        ("weights", None if weights is None else dict(weights)),
        ("types", None if types is None else list(types)),
        ("depth", depth),
    ):
        if value is not None:
            params[key] = value
    body = validate_params(op, SimulationParams, params)
    # Every call is a new simulation; retrying could record it twice.
    return Call(
        operation=op,
        method="POST",
        path="/v2/scenarios",
        json=body,
        options={"max_retries": 0, **(options or {})},
    )


def _get(id: str, options: RequestOptions | None) -> Call:
    return Call(operation="scenarios.get", path=f"/v2/scenarios/{segment(id)}", options=options)


def _list(limit: int | None, offset: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="scenarios.list",
        path="/v2/scenarios",
        params={"limit": limit, "offset": offset},
        options=options,
    )


def _for_decision(
    id: str, limit: int | None, offset: int | None, options: RequestOptions | None
) -> Call:
    return Call(
        operation="scenarios.for_decision",
        path=f"/v2/decisions/{segment(id)}/scenarios",
        params={"limit": limit, "offset": offset},
        options=options,
    )


class Scenarios(SyncAPIResource):
    """The Scenario Simulator: plausible ways to act on a recorded decision.

    Each scenario takes an explicit stance toward the decision's evidence (best,
    base, worst case, aggressive, conservative), states every assumption it
    makes, and is scored on evidence quality, uncertainty, constraints,
    objective alignment, and risk exposure. Planning, not forecasting.
    """

    def simulate(
        self,
        decision_id: str,
        *,
        objective: str | None = None,
        constraints: Sequence[ConstraintParams] | None = None,
        assumptions: Sequence[StatedAssumptionParams] | None = None,
        risk_tolerance: float | None = None,
        weights: ScenarioWeightParams | Mapping[str, float] | None = None,
        types: Sequence[ScenarioType] | None = None,
        depth: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> Simulation:
        """Simulates and ranks scenarios for a decision. Not retried."""
        call = _simulate(
            decision_id,
            objective,
            constraints,
            assumptions,
            risk_tolerance,
            weights,
            types,
            depth,
            request_options,
        )
        return self._transport.request(call, Simulation)

    def get(self, id: str, *, request_options: RequestOptions | None = None) -> Scenario:
        """One scenario with its assumptions and outcomes."""
        return self._transport.request(_get(id, request_options), Scenario)

    def list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> SimulationPage:
        """Simulations across the organization, newest first."""
        return self._transport.request(_list(limit, offset, request_options), SimulationPage)

    def iter(self, *, page_size: int = 100) -> Iterator[SimulationSummary]:
        return paginate(
            lambda limit, offset: self.list(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    def for_decision(
        self,
        id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionScenarios:
        """A decision's simulations, newest first. Defaults to the latest 5."""
        call = _for_decision(id, limit, offset, request_options)
        return self._transport.request(call, DecisionScenarios)


class AsyncScenarios(AsyncAPIResource):
    async def simulate(
        self,
        decision_id: str,
        *,
        objective: str | None = None,
        constraints: Sequence[ConstraintParams] | None = None,
        assumptions: Sequence[StatedAssumptionParams] | None = None,
        risk_tolerance: float | None = None,
        weights: ScenarioWeightParams | Mapping[str, float] | None = None,
        types: Sequence[ScenarioType] | None = None,
        depth: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> Simulation:
        call = _simulate(
            decision_id,
            objective,
            constraints,
            assumptions,
            risk_tolerance,
            weights,
            types,
            depth,
            request_options,
        )
        return await self._transport.request(call, Simulation)

    async def get(self, id: str, *, request_options: RequestOptions | None = None) -> Scenario:
        return await self._transport.request(_get(id, request_options), Scenario)

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> SimulationPage:
        return await self._transport.request(_list(limit, offset, request_options), SimulationPage)

    def iter(self, *, page_size: int = 100) -> AsyncIterator[SimulationSummary]:
        return apaginate(
            lambda limit, offset: self.list(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    async def for_decision(
        self,
        id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionScenarios:
        call = _for_decision(id, limit, offset, request_options)
        return await self._transport.request(call, DecisionScenarios)
