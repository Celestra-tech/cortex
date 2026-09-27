"""Unsaved evidence rows for exercising graph algorithms without a database."""

import uuid
from datetime import UTC, datetime, timedelta

from cortex_api.database.ids import uuid7
from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.services.evidence.graph import EvidenceGraph

ORGANIZATION = uuid.UUID("01900000-0000-7000-8000-000000000001")
EPOCH = datetime(2026, 9, 1, tzinfo=UTC)


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, EvidenceNode] = {}
        self.edges: list[EvidenceEdge] = []
        self._tick = 0

    def _now(self) -> datetime:
        self._tick += 1
        return EPOCH + timedelta(minutes=self._tick)

    def node(
        self, name: str, type_: EvidenceNodeType = EvidenceNodeType.MEMORY, confidence: float = 1.0
    ) -> EvidenceNode:
        node = EvidenceNode(
            id=uuid7(),
            organization_id=ORGANIZATION,
            type=type_,
            ref_id=uuid7(),
            title=name,
            confidence=confidence,
            metadata_={},
        )
        node.created_at = self._now()
        self.nodes[name] = node
        return node

    def edge(
        self,
        upstream: str,
        downstream: str,
        type_: EvidenceEdgeType = EvidenceEdgeType.SUPPORTS,
        confidence: float = 1.0,
    ) -> EvidenceEdge:
        now = self._now()
        edge = EvidenceEdge(
            id=uuid7(),
            organization_id=ORGANIZATION,
            from_node_id=self.nodes[upstream].id,
            to_node_id=self.nodes[downstream].id,
            type=type_,
            confidence=confidence,
            explanation=f"{upstream} {type_} {downstream}",
            source="test",
            observed_at=now,
        )
        edge.created_at = now
        self.edges.append(edge)
        return edge

    def graph(self) -> EvidenceGraph:
        return EvidenceGraph.of(self.nodes.values(), self.edges)

    def id(self, name: str) -> uuid.UUID:
        return self.nodes[name].id
