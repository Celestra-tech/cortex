"""In-memory evidence subgraphs, and loading them from PostgreSQL."""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.repositories.evidence_repository import Direction, EvidenceRepository

DEFAULT_DEPTH = 4
MAX_DEPTH = 10
DEFAULT_MAX_EDGES = 2000


class EvidenceNotFoundError(LookupError):
    def __init__(self, kind: str, identifier: uuid.UUID) -> None:
        super().__init__(f"{kind} {identifier} not found")
        self.kind = kind
        self.identifier = identifier


@dataclass(slots=True)
class EvidenceGraph:
    """A subgraph with adjacency in both directions.

    Edges point upstream to downstream: from the evidence to what it informed.
    `truncated` is set when the loader stopped at its edge budget, so the
    graph is a faithful but partial view.
    """

    nodes: dict[uuid.UUID, EvidenceNode] = field(default_factory=dict)
    edges: dict[uuid.UUID, EvidenceEdge] = field(default_factory=dict)
    truncated: bool = False
    _incoming: dict[uuid.UUID, list[EvidenceEdge]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _outgoing: dict[uuid.UUID, list[EvidenceEdge]] = field(
        default_factory=lambda: defaultdict(list)
    )

    @classmethod
    def of(
        cls,
        nodes: Iterable[EvidenceNode],
        edges: Iterable[EvidenceEdge],
        *,
        truncated: bool = False,
    ) -> "EvidenceGraph":
        graph = cls(truncated=truncated)
        graph.add(nodes, edges)
        return graph

    def add(self, nodes: Iterable[EvidenceNode], edges: Iterable[EvidenceEdge]) -> None:
        for node in nodes:
            self.nodes[node.id] = node
        for edge in edges:
            if edge.id in self.edges:
                continue
            self.edges[edge.id] = edge
            self._incoming[edge.to_node_id].append(edge)
            self._outgoing[edge.from_node_id].append(edge)

    def merge(self, other: "EvidenceGraph") -> None:
        self.add(other.nodes.values(), other.edges.values())
        self.truncated = self.truncated or other.truncated

    def incoming(self, node_id: uuid.UUID) -> list[EvidenceEdge]:
        """Edges from the evidence behind `node_id`."""
        return self._incoming.get(node_id, [])

    def outgoing(self, node_id: uuid.UUID) -> list[EvidenceEdge]:
        """Edges to what `node_id` informed."""
        return self._outgoing.get(node_id, [])

    def steps(
        self,
        node_id: uuid.UUID,
        direction: Direction,
        edge_types: frozenset[EvidenceEdgeType] | None = None,
    ) -> Iterator[tuple[EvidenceEdge, uuid.UUID]]:
        """Each edge leaving `node_id` in `direction`, with the node it reaches."""
        if direction in (Direction.UPSTREAM, Direction.BOTH):
            for edge in self.incoming(node_id):
                if edge_types is None or edge.type in edge_types:
                    yield edge, edge.from_node_id
        if direction in (Direction.DOWNSTREAM, Direction.BOTH):
            for edge in self.outgoing(node_id):
                if edge_types is None or edge.type in edge_types:
                    yield edge, edge.to_node_id


class EvidenceGraphLoader:
    """Loads the neighborhood of a node, bounded by depth and an edge budget."""

    def __init__(self, repository: EvidenceRepository, *, max_edges: int = DEFAULT_MAX_EDGES):
        self.repository = repository
        self.max_edges = max_edges

    async def node(self, organization_id: uuid.UUID, node_id: uuid.UUID) -> EvidenceNode:
        node = await self.repository.get_node(organization_id, node_id)
        if node is None:
            raise EvidenceNotFoundError("Evidence node", node_id)
        return node

    async def decision(self, organization_id: uuid.UUID, decision_id: uuid.UUID) -> EvidenceNode:
        """A decision by the id of what it decided (for completions, the completion id)."""
        node = await self.repository.get_by_ref(
            organization_id, EvidenceNodeType.DECISION, decision_id
        )
        if node is None:
            raise EvidenceNotFoundError("Decision", decision_id)
        return node

    async def around(
        self,
        organization_id: uuid.UUID,
        root: EvidenceNode,
        *,
        direction: Direction,
        depth: int = DEFAULT_DEPTH,
        edge_types: Iterable[EvidenceEdgeType] | None = None,
    ) -> EvidenceGraph:
        depth = max(0, min(depth, MAX_DEPTH))
        edges = await self.repository.walk(
            organization_id,
            root.id,
            direction=direction,
            max_depth=depth,
            edge_types=edge_types,
            max_edges=self.max_edges + 1,
        )
        truncated = len(edges) > self.max_edges
        edges = edges[: self.max_edges]
        ids = {root.id} | {e.from_node_id for e in edges} | {e.to_node_id for e in edges}
        nodes = await self.repository.get_nodes(organization_id, ids - {root.id})
        return EvidenceGraph.of([root, *nodes.values()], edges, truncated=truncated)

    async def decision_graph(
        self, organization_id: uuid.UUID, decision: EvidenceNode, *, depth: int = DEFAULT_DEPTH
    ) -> EvidenceGraph:
        """The evidence behind a decision, plus what the decision went on to produce.

        Walking both ways at once would climb to a shared document and descend
        into every other decision that cited it, so the two sides are loaded
        separately and merged.
        """
        graph = await self.around(
            organization_id, decision, direction=Direction.UPSTREAM, depth=depth
        )
        graph.merge(
            await self.around(
                organization_id, decision, direction=Direction.DOWNSTREAM, depth=depth
            )
        )
        return graph
