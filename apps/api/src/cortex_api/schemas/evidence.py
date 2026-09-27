import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.services.evidence.provenance import (
    MAX_EXPLANATION_LENGTH,
    MAX_SOURCE_LENGTH,
    TimelineEvent,
    TimelineKind,
    occurred_at,
)
from cortex_api.services.evidence.traversal import Contradiction, Reached, SupportingEvidence

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Explanation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_EXPLANATION_LENGTH),
]
# Leaves room for the " via api:key/<uuid>" suffix within MAX_SOURCE_LENGTH.
SourceName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_SOURCE_LENGTH - 64)
]


class EvidenceNodeRead(BaseModel):
    id: uuid.UUID
    type: EvidenceNodeType
    ref_id: uuid.UUID | None = Field(description="The record this node stands for.")
    title: str
    confidence: float
    created_at: datetime
    occurred_at: datetime = Field(description="When the underlying record came to be.")
    metadata: dict[str, Any]

    @classmethod
    def of(cls, node: EvidenceNode) -> "EvidenceNodeRead":
        return cls(
            id=node.id,
            type=node.type,
            ref_id=node.ref_id,
            title=node.title,
            confidence=node.confidence,
            created_at=node.created_at,
            occurred_at=occurred_at(node),
            metadata=node.metadata_,
        )


class ProvenanceRead(BaseModel):
    confidence: float
    explanation: str
    source: str = Field(description="The component or API key that asserted the link.")
    timestamp: datetime = Field(description="When the relationship was observed.")


class EvidenceEdgeRead(BaseModel):
    id: uuid.UUID
    type: EvidenceEdgeType
    from_node_id: uuid.UUID = Field(description="Upstream: the evidence.")
    to_node_id: uuid.UUID = Field(description="Downstream: what the evidence informed.")
    provenance: ProvenanceRead
    created_at: datetime

    @classmethod
    def of(cls, edge: EvidenceEdge) -> "EvidenceEdgeRead":
        return cls(
            id=edge.id,
            type=edge.type,
            from_node_id=edge.from_node_id,
            to_node_id=edge.to_node_id,
            provenance=ProvenanceRead(
                confidence=edge.confidence,
                explanation=edge.explanation,
                source=edge.source,
                timestamp=edge.observed_at,
            ),
            created_at=edge.created_at,
        )


class SupportingEvidenceRead(BaseModel):
    node: EvidenceNodeRead
    depth: int = Field(description="Hops to the decision along the strongest route.")
    path_confidence: float = Field(description="Product of edge confidences along that route.")
    strength: float = Field(description="path_confidence times the node's own confidence.")
    path: list[uuid.UUID] = Field(description="Edge ids of the route, from this node onward.")

    @classmethod
    def of(cls, item: SupportingEvidence) -> "SupportingEvidenceRead":
        return cls(
            node=EvidenceNodeRead.of(item.node),
            depth=item.depth,
            path_confidence=item.path_confidence,
            strength=item.strength,
            path=[edge.id for edge in item.path],
        )


class ContradictionRead(BaseModel):
    node: EvidenceNodeRead
    edge: EvidenceEdgeRead

    @classmethod
    def of(cls, item: Contradiction) -> "ContradictionRead":
        return cls(node=EvidenceNodeRead.of(item.node), edge=EvidenceEdgeRead.of(item.edge))


class DecisionEvidenceResponse(BaseModel):
    decision: EvidenceNodeRead
    supporting: list[SupportingEvidenceRead] = Field(description="Strongest first.")
    contradicting: list[ContradictionRead]
    counts: dict[EvidenceNodeType, int] = Field(description="Supporting evidence by node type.")


class DecisionListResponse(BaseModel):
    items: list[EvidenceNodeRead]
    total: int
    limit: int
    offset: int


class GraphNodeRead(EvidenceNodeRead):
    depth: int = Field(
        description="Signed hops from the root: negative upstream (evidence), positive downstream."
    )


class TimelineEventRead(BaseModel):
    at: datetime
    kind: TimelineKind
    id: uuid.UUID = Field(description="The node or edge id.")
    label: str
    source: str | None
    confidence: float

    @classmethod
    def of(cls, event: TimelineEvent) -> "TimelineEventRead":
        return cls(
            at=event.at,
            kind=event.kind,
            id=event.id,
            label=event.label,
            source=event.source,
            confidence=event.confidence,
        )


class EvidenceGraphResponse(BaseModel):
    root_id: uuid.UUID
    depth: int
    nodes: list[GraphNodeRead]
    edges: list[EvidenceEdgeRead]
    timeline: list[TimelineEventRead] = Field(description="Records and links in time order.")
    truncated: bool = Field(description="True when the edge budget cut the graph short.")


class NeighborRead(BaseModel):
    edge: EvidenceEdgeRead
    node: EvidenceNodeRead


class ReachedRead(BaseModel):
    node: EvidenceNodeRead
    depth: int

    @classmethod
    def of(cls, item: Reached) -> "ReachedRead":
        return cls(node=EvidenceNodeRead.of(item.node), depth=item.depth)


class EvidenceNodeDetailResponse(BaseModel):
    node: EvidenceNodeRead
    upstream: list[NeighborRead] = Field(description="Direct evidence behind this node.")
    downstream: list[NeighborRead] = Field(description="What this node directly informed.")
    decisions: list[ReachedRead] = Field(description="Decisions this node contributed to.")


class EvidencePathResponse(BaseModel):
    source_id: uuid.UUID
    target_id: uuid.UUID
    connected: bool
    edges: list[EvidenceEdgeRead] = Field(description="In order from source to target.")
    nodes: list[EvidenceNodeRead] = Field(description="Every node on the path, in order.")


class EvidenceInput(BaseModel):
    """One piece of evidence behind a recorded decision."""

    type: EvidenceNodeType
    ref_id: uuid.UUID | None = Field(
        default=None,
        description="The record this evidence stands for. Evidence with the same type and "
        "ref_id is shared across decisions.",
    )
    title: Title
    confidence: Confidence = Field(default=1.0, description="How reliable the evidence is.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    relation: EvidenceEdgeType = EvidenceEdgeType.SUPPORTS
    relation_confidence: Confidence = Field(
        default=1.0, description="How strongly the evidence bears on the decision."
    )
    explanation: Explanation
    observed_at: datetime | None = Field(default=None, description="Defaults to now.")


class DecisionCreate(BaseModel):
    ref_id: uuid.UUID | None = Field(
        default=None,
        description="The id of the decision in your system; generated when omitted. "
        "It addresses the decision in every evidence endpoint.",
    )
    title: Title
    confidence: Confidence | None = Field(
        default=None,
        description="Omit to derive it from the supporting and contradicting evidence.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: SourceName | None = Field(
        default=None,
        description="Who asserts these links, e.g. `policy-engine@2`. Recorded alongside the "
        "API key; defaults to the key alone.",
    )
    evidence: list[EvidenceInput] = Field(min_length=1, max_length=200)
