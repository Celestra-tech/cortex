"""Scenarios, their assumptions, and outcomes, grouped into simulations."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cortex_api.models.assumption import Assumption
from cortex_api.models.evidence_node import EvidenceNode
from cortex_api.models.outcome import Outcome
from cortex_api.models.scenario import Scenario


@dataclass(frozen=True)
class SimulationSummary:
    """The recommended scenario of one simulation, with its decision."""

    recommended: Scenario
    decision_title: str
    scenario_count: int


CHILDREN = (selectinload(Scenario.assumptions), selectinload(Scenario.outcomes))


class ScenarioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_simulation(
        self,
        scenarios: Sequence[Scenario],
        assumptions: Sequence[Assumption],
        outcomes: Sequence[Outcome],
    ) -> None:
        """Stages one simulation. Ids must be set so outcomes can name their assumptions."""
        self.session.add_all(scenarios)
        await self.session.flush()
        self.session.add_all(assumptions)
        await self.session.flush()
        self.session.add_all(outcomes)
        await self.session.flush()

    async def get(self, organization_id: uuid.UUID, scenario_id: uuid.UUID) -> Scenario | None:
        return await self.session.scalar(
            select(Scenario)
            .where(Scenario.organization_id == organization_id, Scenario.id == scenario_id)
            .options(*CHILDREN)
        )

    async def simulation(
        self, organization_id: uuid.UUID, simulation_id: uuid.UUID
    ) -> list[Scenario]:
        """Every scenario of one simulation, best first."""
        rows = await self.session.scalars(
            select(Scenario)
            .where(
                Scenario.organization_id == organization_id,
                Scenario.simulation_id == simulation_id,
            )
            .order_by(Scenario.rank)
            .options(*CHILDREN)
        )
        return list(rows)

    async def for_decision(
        self,
        organization_id: uuid.UUID,
        decision_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[list[Scenario]]:
        """The decision's simulations, newest first, each as its scenarios best first."""
        recommended = await self.session.scalars(
            select(Scenario)
            .where(
                Scenario.organization_id == organization_id,
                Scenario.decision_id == decision_id,
                Scenario.rank == 1,
            )
            .order_by(Scenario.created_at.desc(), Scenario.simulation_id.desc())
            .limit(limit)
            .offset(offset)
        )
        simulation_ids = [scenario.simulation_id for scenario in recommended]
        if not simulation_ids:
            return []
        rows = await self.session.scalars(
            select(Scenario)
            .where(
                Scenario.organization_id == organization_id,
                Scenario.simulation_id.in_(simulation_ids),
            )
            .order_by(Scenario.rank)
            .options(*CHILDREN)
        )
        grouped: dict[uuid.UUID, list[Scenario]] = {sid: [] for sid in simulation_ids}
        for scenario in rows:
            grouped[scenario.simulation_id].append(scenario)
        return [grouped[sid] for sid in simulation_ids]

    async def count_for_decision(self, organization_id: uuid.UUID, decision_id: uuid.UUID) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(Scenario)
            .where(
                Scenario.organization_id == organization_id,
                Scenario.decision_id == decision_id,
                Scenario.rank == 1,
            )
        )
        return total or 0

    async def list_simulations(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> list[SimulationSummary]:
        """Every simulation in the organization, newest first."""
        sizes = (
            select(Scenario.simulation_id, func.count().label("size"))
            .where(Scenario.organization_id == organization_id)
            .group_by(Scenario.simulation_id)
            .subquery()
        )
        rows = await self.session.execute(
            select(Scenario, EvidenceNode.title, sizes.c.size)
            .join(EvidenceNode, EvidenceNode.id == Scenario.decision_node_id)
            .join(sizes, sizes.c.simulation_id == Scenario.simulation_id)
            .where(Scenario.organization_id == organization_id, Scenario.rank == 1)
            .order_by(Scenario.created_at.desc(), Scenario.simulation_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [
            SimulationSummary(recommended=scenario, decision_title=title, scenario_count=size)
            for scenario, title, size in rows.all()
        ]

    async def count_simulations(self, organization_id: uuid.UUID) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(Scenario)
            .where(Scenario.organization_id == organization_id, Scenario.rank == 1)
        )
        return total or 0
