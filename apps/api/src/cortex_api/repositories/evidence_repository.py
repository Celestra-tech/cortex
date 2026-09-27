import uuid
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, Uuid, case, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType


class Direction(StrEnum):
    UPSTREAM = "upstream"
    """Toward the evidence a node was produced from (its ancestors)."""
    DOWNSTREAM = "downstream"
    """Toward what a node informed (its descendants)."""
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class NodeValues:
    type: EvidenceNodeType
    title: str
    confidence: float
    ref_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EdgeValues:
    from_node_id: uuid.UUID
    to_node_id: uuid.UUID
    type: EvidenceEdgeType
    confidence: float
    explanation: str
    source: str
    observed_at: datetime


class EvidenceRepository:
    """Persistence for the evidence graph. Flushes, never commits.

    Nodes with a `ref_id` are unique per organization and type, and edges are
    unique per (from, to, type). Writes are idempotent upserts that keep the
    first recorded version: evidence is a historical record, so re-linking the
    same relationship never rewrites its provenance.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Nodes -------------------------------------------------------------

    async def get_node(self, organization_id: uuid.UUID, node_id: uuid.UUID) -> EvidenceNode | None:
        result = await self.session.execute(
            select(EvidenceNode).where(
                EvidenceNode.organization_id == organization_id, EvidenceNode.id == node_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_ref(
        self, organization_id: uuid.UUID, type_: EvidenceNodeType, ref_id: uuid.UUID
    ) -> EvidenceNode | None:
        result = await self.session.execute(
            select(EvidenceNode).where(
                EvidenceNode.organization_id == organization_id,
                EvidenceNode.type == type_,
                EvidenceNode.ref_id == ref_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_nodes(
        self, organization_id: uuid.UUID, node_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, EvidenceNode]:
        if not node_ids:
            return {}
        result = await self.session.execute(
            select(EvidenceNode).where(
                EvidenceNode.organization_id == organization_id,
                EvidenceNode.id.in_(node_ids),
            )
        )
        return {node.id: node for node in result.scalars()}

    def _decisions(self, organization_id: uuid.UUID) -> Select[EvidenceNode]:
        return select(EvidenceNode).where(
            EvidenceNode.organization_id == organization_id,
            EvidenceNode.type == EvidenceNodeType.DECISION,
        )

    async def list_decisions(
        self, organization_id: uuid.UUID, *, limit: int, offset: int
    ) -> Sequence[EvidenceNode]:
        result = await self.session.execute(
            self._decisions(organization_id)
            .order_by(EvidenceNode.created_at.desc(), EvidenceNode.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def count_decisions(self, organization_id: uuid.UUID) -> int:
        subquery = self._decisions(organization_id).subquery()
        result = await self.session.execute(select(func.count()).select_from(subquery))
        return result.scalar_one()

    async def upsert_nodes(
        self, organization_id: uuid.UUID, values: Sequence[NodeValues]
    ) -> list[EvidenceNode]:
        """One node per input, in input order; existing referenced nodes are reused."""
        if not values:
            return []
        rows = [
            {
                "organization_id": organization_id,
                "type": v.type,
                "ref_id": v.ref_id,
                "title": v.title,
                "confidence": v.confidence,
                "metadata_": v.metadata,
            }
            for v in values
        ]
        referenced = [row for row in rows if row["ref_id"] is not None]
        anonymous = [row for row in rows if row["ref_id"] is None]

        if referenced:
            await self.session.execute(
                insert(EvidenceNode)
                .values(_dedupe(referenced, key=lambda r: (r["type"], r["ref_id"])))
                .on_conflict_do_nothing(
                    index_elements=["organization_id", "type", "ref_id"],
                    index_where=EvidenceNode.ref_id.is_not(None),
                )
            )
        created: list[EvidenceNode] = []
        if anonymous:
            inserted = await self.session.scalars(
                insert(EvidenceNode).returning(EvidenceNode), anonymous
            )
            created = list(inserted)

        by_ref: dict[tuple[EvidenceNodeType, uuid.UUID], EvidenceNode] = {}
        if referenced:
            keys = {(row["type"], row["ref_id"]) for row in referenced}
            result = await self.session.execute(
                select(EvidenceNode).where(
                    EvidenceNode.organization_id == organization_id,
                    tuple_(EvidenceNode.type, EvidenceNode.ref_id).in_(list(keys)),
                )
            )
            by_ref = {(node.type, node.ref_id): node for node in result.scalars() if node.ref_id}

        fresh = iter(created)
        ordered: list[EvidenceNode] = []
        for v in values:
            ordered.append(by_ref[(v.type, v.ref_id)] if v.ref_id is not None else next(fresh))
        return ordered

    # --- Edges -------------------------------------------------------------

    async def upsert_edges(
        self, organization_id: uuid.UUID, values: Sequence[EdgeValues]
    ) -> list[EvidenceEdge]:
        """One edge per input, in input order; an existing edge keeps its provenance."""
        if not values:
            return []
        rows = [
            {
                "organization_id": organization_id,
                "from_node_id": v.from_node_id,
                "to_node_id": v.to_node_id,
                "type": v.type,
                "confidence": v.confidence,
                "explanation": v.explanation,
                "source": v.source,
                "observed_at": v.observed_at,
            }
            for v in values
        ]

        def key(row: dict[str, Any]) -> tuple[Any, ...]:
            return (row["from_node_id"], row["to_node_id"], row["type"])

        unique = _dedupe(rows, key=key)
        await self.session.execute(
            insert(EvidenceEdge)
            .values(unique)
            .on_conflict_do_nothing(index_elements=["from_node_id", "to_node_id", "type"])
        )
        result = await self.session.execute(
            select(EvidenceEdge).where(
                EvidenceEdge.organization_id == organization_id,
                tuple_(EvidenceEdge.from_node_id, EvidenceEdge.to_node_id, EvidenceEdge.type).in_(
                    [key(r) for r in unique]
                ),
            )
        )
        by_key = {(e.from_node_id, e.to_node_id, e.type): e for e in result.scalars()}
        return [by_key[key(r)] for r in rows]

    async def edges_touching(
        self,
        organization_id: uuid.UUID,
        node_ids: Collection[uuid.UUID],
        *,
        direction: Direction = Direction.BOTH,
        limit: int | None = None,
    ) -> Sequence[EvidenceEdge]:
        if not node_ids:
            return []
        condition: ColumnElement[bool]
        if direction is Direction.UPSTREAM:
            condition = EvidenceEdge.to_node_id.in_(node_ids)
        elif direction is Direction.DOWNSTREAM:
            condition = EvidenceEdge.from_node_id.in_(node_ids)
        else:
            condition = EvidenceEdge.to_node_id.in_(node_ids) | EvidenceEdge.from_node_id.in_(
                node_ids
            )
        statement = (
            select(EvidenceEdge)
            .where(EvidenceEdge.organization_id == organization_id, condition)
            .order_by(EvidenceEdge.observed_at, EvidenceEdge.id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def walk(
        self,
        organization_id: uuid.UUID,
        start_id: uuid.UUID,
        *,
        direction: Direction,
        max_depth: int,
        edge_types: Iterable[EvidenceEdgeType] | None = None,
        max_edges: int,
    ) -> Sequence[EvidenceEdge]:
        """Every edge within `max_depth` hops of `start_id`, via one recursive query.

        UNION (not UNION ALL) discards repeated (edge, node, depth) rows and the
        depth bound stops cycles, so the walk terminates on any graph shape.
        """
        if max_depth < 1:
            return []
        edges = EvidenceEdge.__table__
        filters: list[ColumnElement[bool]] = [edges.c.organization_id == organization_id]
        if edge_types is not None:
            filters.append(edges.c.type.in_([str(t) for t in edge_types]))

        def hop(anchor: ColumnElement[uuid.UUID]) -> tuple[ColumnElement[bool], Any]:
            """Which edges leave `anchor` in the walk direction, and the node they reach."""
            if direction is Direction.UPSTREAM:
                return edges.c.to_node_id == anchor, edges.c.from_node_id
            if direction is Direction.DOWNSTREAM:
                return edges.c.from_node_id == anchor, edges.c.to_node_id
            # PostgreSQL allows one recursive reference, so both directions share a join.
            far = case(
                (edges.c.to_node_id == anchor, edges.c.from_node_id), else_=edges.c.to_node_id
            )
            return or_(edges.c.to_node_id == anchor, edges.c.from_node_id == anchor), far

        seed_on, seed_far = hop(literal(start_id, type_=Uuid()))
        walk = (
            select(
                edges.c.id.label("edge_id"),
                seed_far.label("node_id"),
                literal(1).label("depth"),
            )
            .where(seed_on, *filters)
            .cte("evidence_walk", recursive=True)
        )
        previous = walk.alias("previous")
        step_on, step_far = hop(previous.c.node_id)
        walk = walk.union(
            select(edges.c.id, step_far, previous.c.depth + 1)
            .join(previous, step_on)
            .where(previous.c.depth < max_depth, *filters)
        )

        nearest = (
            select(walk.c.edge_id, func.min(walk.c.depth).label("depth"))
            .group_by(walk.c.edge_id)
            .subquery()
        )
        result = await self.session.execute(
            select(EvidenceEdge)
            .join(nearest, nearest.c.edge_id == EvidenceEdge.id)
            .order_by(nearest.c.depth, EvidenceEdge.observed_at, EvidenceEdge.id)
            .limit(max_edges)
        )
        return result.scalars().all()


def _dedupe(rows: list[dict[str, Any]], *, key: Any) -> list[dict[str, Any]]:
    seen: set[Any] = set()
    unique = []
    for row in rows:
        k = key(row)
        if k not in seen:
            seen.add(k)
            unique.append(row)
    return unique
