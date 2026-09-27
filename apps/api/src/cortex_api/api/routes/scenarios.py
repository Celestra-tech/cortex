"""The Scenario Simulator: plausible paths for acting on a decision.

Scenario planning, not forecasting. Every score traces back to explicit
assumptions (each with a confidence and a source) and outcomes (each with an
impact and a likelihood), so a reader can disagree with any input and see
exactly what it changes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from cortex_api.api.deps import OrganizationDep, PrincipalDep
from cortex_api.database.session import DbSession
from cortex_api.models.scenario import Scenario
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.evidence_repository import EvidenceRepository
from cortex_api.repositories.scenario_repository import ScenarioRepository
from cortex_api.schemas.scenario import (
    DecisionScenariosResponse,
    ScenarioRead,
    SimulationCreate,
    SimulationListResponse,
    SimulationRead,
    SimulationSummaryRead,
)
from cortex_api.services.evidence.graph import EvidenceGraphLoader
from cortex_api.services.evidence.provenance import api_source
from cortex_api.services.scenario.assumptions import Constraint, StatedAssumption
from cortex_api.services.scenario.simulator import ScenarioSimulator, SimulationRequest

router = APIRouter(prefix="/scenarios", tags=["scenarios"])
decisions_router = APIRouter(prefix="/decisions", tags=["scenarios"])


def get_repository(session: DbSession) -> ScenarioRepository:
    return ScenarioRepository(session)


RepositoryDep = Annotated[ScenarioRepository, Depends(get_repository)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.post(
    "",
    response_model=SimulationRead,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"description": "No decision with this id."}},
)
async def simulate(
    body: SimulationCreate, principal: PrincipalDep, session: DbSession
) -> SimulationRead:
    """Generates and ranks scenarios for a decision from its recorded evidence.

    Each scenario applies a stated stance (best, base, and worst case; an
    aggressive and a conservative strategy) to the evidence, your
    constraints, and your assumptions, then scores the result on evidence
    quality, uncertainty, constraint satisfaction, objective alignment, and
    risk exposure.
    """
    request = SimulationRequest(
        decision_id=body.decision_id,
        objective=body.objective,
        constraints=[Constraint(c.statement, c.severity) for c in body.constraints],
        stated=[StatedAssumption(a.statement, a.confidence) for a in body.assumptions],
        risk_tolerance=body.risk_tolerance,
        weights=body.weights.overrides() if body.weights else {},
        types=body.types,
        depth=body.depth,
    )
    simulation = await ScenarioSimulator(session).simulate(
        principal.organization.id, request, source=api_source(principal.api_key_id)
    )
    await session.commit()
    return SimulationRead.of(simulation.scenarios)


@router.get("", response_model=SimulationListResponse)
async def list_simulations(
    organization: OrganizationDep,
    repository: RepositoryDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> SimulationListResponse:
    """Every simulation, newest first, with its recommended scenario."""
    summaries = await repository.list_simulations(organization.id, limit=limit, offset=offset)
    return SimulationListResponse(
        items=[SimulationSummaryRead.of(s) for s in summaries],
        total=await repository.count_simulations(organization.id),
        limit=limit,
        offset=offset,
    )


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(
    scenario_id: uuid.UUID, organization: OrganizationDep, repository: RepositoryDep
) -> ScenarioRead:
    """One scenario with its assumptions, outcomes, and score breakdown."""
    scenario = await repository.get(organization.id, scenario_id)
    if scenario is None:
        raise NotFoundError(Scenario, scenario_id)
    return ScenarioRead.of(scenario)


@decisions_router.get(
    "/{decision_id}/scenarios",
    response_model=DecisionScenariosResponse,
    responses={404: {"description": "No decision with this id."}},
)
async def decision_scenarios(
    decision_id: uuid.UUID,
    organization: OrganizationDep,
    repository: RepositoryDep,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    offset: Offset = 0,
) -> DecisionScenariosResponse:
    """The decision's simulations, newest first; each lists its scenarios best first."""
    await EvidenceGraphLoader(EvidenceRepository(session)).decision(organization.id, decision_id)
    simulations = await repository.for_decision(
        organization.id, decision_id, limit=limit, offset=offset
    )
    return DecisionScenariosResponse(
        decision_id=decision_id,
        simulations=[SimulationRead.of(s) for s in simulations],
        total=await repository.count_for_decision(organization.id, decision_id),
        limit=limit,
        offset=offset,
    )
