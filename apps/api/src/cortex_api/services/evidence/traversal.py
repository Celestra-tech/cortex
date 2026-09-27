"""Graph traversal: what produced a node, what it produced, and how two nodes connect."""

import heapq
import uuid
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode
from cortex_api.repositories.evidence_repository import Direction
from cortex_api.services.evidence.graph import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    EvidenceGraph,
    EvidenceGraphLoader,
)

SUPPORTING_TYPES = frozenset(EvidenceEdgeType) - {EvidenceEdgeType.CONTRADICTS}


@dataclass(frozen=True, slots=True)
class Reached:
    node: EvidenceNode
    depth: int
    """Hops from the start along the shortest route."""


@dataclass(frozen=True, slots=True)
class SupportingEvidence:
    node: EvidenceNode
    depth: int
    path_confidence: float
    """How reliably the strongest route carries this evidence to the decision: the
    product of its edge confidences and of the confidences of the nodes it passes
    through, so evidence reached via a weak intermediary is discounted by it."""
    path: tuple[EvidenceEdge, ...]
    """That route, from this node down to the decision."""

    @property
    def strength(self) -> float:
        """How much this evidence backs the decision: route confidence times its own."""
        return self.path_confidence * self.node.confidence


@dataclass(frozen=True, slots=True)
class Contradiction:
    node: EvidenceNode
    edge: EvidenceEdge


def _reach(
    graph: EvidenceGraph,
    start: uuid.UUID,
    direction: Direction,
    max_depth: int | None,
    edge_types: frozenset[EvidenceEdgeType] | None,
) -> list[Reached]:
    depths = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        depth = depths[current]
        if max_depth is not None and depth >= max_depth:
            continue
        for _, neighbor in graph.steps(current, direction, edge_types):
            if neighbor not in depths and neighbor in graph.nodes:
                depths[neighbor] = depth + 1
                queue.append(neighbor)
    reached = [Reached(graph.nodes[n], d) for n, d in depths.items() if n != start]
    reached.sort(key=lambda r: (r.depth, r.node.created_at, str(r.node.id)))
    return reached


def ancestors(
    graph: EvidenceGraph,
    node_id: uuid.UUID,
    *,
    max_depth: int | None = None,
    edge_types: Iterable[EvidenceEdgeType] | None = None,
) -> list[Reached]:
    """Everything `node_id` was produced from, nearest first."""
    types = frozenset(edge_types) if edge_types is not None else None
    return _reach(graph, node_id, Direction.UPSTREAM, max_depth, types)


def descendants(
    graph: EvidenceGraph,
    node_id: uuid.UUID,
    *,
    max_depth: int | None = None,
    edge_types: Iterable[EvidenceEdgeType] | None = None,
) -> list[Reached]:
    """Everything `node_id` went on to inform, nearest first."""
    types = frozenset(edge_types) if edge_types is not None else None
    return _reach(graph, node_id, Direction.DOWNSTREAM, max_depth, types)


def shortest_path(
    graph: EvidenceGraph,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    directed: bool = False,
) -> list[EvidenceEdge] | None:
    """The fewest edges connecting two nodes, or None if they are not connected.

    Undirected by default, which answers "how is this document related to
    that decision?". `directed=True` only follows edges downstream, from
    `source_id` toward what it informed.
    """
    if source_id not in graph.nodes or target_id not in graph.nodes:
        return None
    if source_id == target_id:
        return []
    direction = Direction.DOWNSTREAM if directed else Direction.BOTH
    via: dict[uuid.UUID, EvidenceEdge | None] = {source_id: None}
    queue = deque([source_id])
    while queue:
        current = queue.popleft()
        for edge, neighbor in graph.steps(current, direction):
            if neighbor in via:
                continue
            via[neighbor] = edge
            if neighbor == target_id:
                return _unwind(via, source_id, target_id)
            queue.append(neighbor)
    return None


def _unwind(
    via: dict[uuid.UUID, EvidenceEdge | None], source_id: uuid.UUID, target_id: uuid.UUID
) -> list[EvidenceEdge]:
    path: list[EvidenceEdge] = []
    current = target_id
    while current != source_id:
        edge = via[current]
        if edge is None:  # pragma: no cover - only the source has no predecessor
            break
        path.append(edge)
        current = edge.from_node_id if edge.to_node_id == current else edge.to_node_id
    path.reverse()
    return path


def supporting_evidence(
    graph: EvidenceGraph, decision_id: uuid.UUID, *, max_depth: int | None = None
) -> list[SupportingEvidence]:
    """Everything upstream of a decision, ranked by how strongly it backs it.

    Routes through a `contradicts` edge are not support and are excluded; see
    `contradicting_evidence`. Each node keeps its strongest route, found with
    a max-product Dijkstra: confidences are at most 1, so extending a route
    never makes it stronger, which is the property Dijkstra needs.
    """
    best: dict[uuid.UUID, float] = {decision_id: 1.0}
    hops: dict[uuid.UUID, int] = {decision_id: 0}
    via: dict[uuid.UUID, EvidenceEdge] = {}
    settled: set[uuid.UUID] = set()
    frontier: list[tuple[float, int, str, uuid.UUID]] = [(-1.0, 0, str(decision_id), decision_id)]
    while frontier:
        negative, depth, _, current = heapq.heappop(frontier)
        if current in settled:
            continue
        settled.add(current)
        if max_depth is not None and depth >= max_depth:
            continue
        through = 1.0 if current == decision_id else graph.nodes[current].confidence
        for edge, upstream in graph.steps(current, Direction.UPSTREAM, SUPPORTING_TYPES):
            if upstream in settled or upstream not in graph.nodes:
                continue
            confidence = -negative * through * edge.confidence
            if confidence > best.get(upstream, -1.0):
                best[upstream] = confidence
                hops[upstream] = depth + 1
                via[upstream] = edge
                heapq.heappush(frontier, (-confidence, depth + 1, str(upstream), upstream))

    results = []
    for node_id, confidence in best.items():
        if node_id == decision_id:
            continue
        path: list[EvidenceEdge] = []
        current = node_id
        while current != decision_id:
            edge = via[current]
            path.append(edge)
            current = edge.to_node_id
        results.append(
            SupportingEvidence(
                node=graph.nodes[node_id],
                depth=hops[node_id],
                path_confidence=confidence,
                path=tuple(path),
            )
        )
    results.sort(key=lambda s: (-s.strength, s.depth, str(s.node.id)))
    return results


def contradicting_evidence(graph: EvidenceGraph, node_id: uuid.UUID) -> list[Contradiction]:
    """Direct evidence that argues against `node_id`, strongest first."""
    found = [
        Contradiction(graph.nodes[edge.from_node_id], edge)
        for edge in graph.incoming(node_id)
        if edge.type is EvidenceEdgeType.CONTRADICTS and edge.from_node_id in graph.nodes
    ]
    found.sort(key=lambda c: -(c.edge.confidence * c.node.confidence))
    return found


class EvidenceTraversal:
    """The traversal functions over the stored graph, loading only what each needs."""

    def __init__(self, loader: EvidenceGraphLoader) -> None:
        self.loader = loader

    async def ancestors(
        self, organization_id: uuid.UUID, node_id: uuid.UUID, *, max_depth: int = DEFAULT_DEPTH
    ) -> list[Reached]:
        root = await self.loader.node(organization_id, node_id)
        graph = await self.loader.around(
            organization_id, root, direction=Direction.UPSTREAM, depth=max_depth
        )
        return ancestors(graph, root.id, max_depth=max_depth)

    async def descendants(
        self, organization_id: uuid.UUID, node_id: uuid.UUID, *, max_depth: int = DEFAULT_DEPTH
    ) -> list[Reached]:
        root = await self.loader.node(organization_id, node_id)
        graph = await self.loader.around(
            organization_id, root, direction=Direction.DOWNSTREAM, depth=max_depth
        )
        return descendants(graph, root.id, max_depth=max_depth)

    async def shortest_path(
        self,
        organization_id: uuid.UUID,
        source_id: uuid.UUID,
        target_id: uuid.UUID,
        *,
        max_depth: int = MAX_DEPTH,
        directed: bool = False,
    ) -> list[EvidenceEdge] | None:
        source = await self.loader.node(organization_id, source_id)
        await self.loader.node(organization_id, target_id)
        graph = await self.loader.around(
            organization_id,
            source,
            direction=Direction.DOWNSTREAM if directed else Direction.BOTH,
            depth=max_depth,
        )
        return shortest_path(graph, source_id, target_id, directed=directed)

    async def supporting_evidence(
        self, organization_id: uuid.UUID, decision_id: uuid.UUID, *, max_depth: int = DEFAULT_DEPTH
    ) -> list[SupportingEvidence]:
        decision = await self.loader.decision(organization_id, decision_id)
        graph = await self.loader.around(
            organization_id,
            decision,
            direction=Direction.UPSTREAM,
            depth=max_depth,
            edge_types=SUPPORTING_TYPES,
        )
        return supporting_evidence(graph, decision.id, max_depth=max_depth)
