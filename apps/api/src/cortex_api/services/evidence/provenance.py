"""Provenance: who asserted each link, how strongly, why, and when."""

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode

MAX_SOURCE_LENGTH = 255
MAX_EXPLANATION_LENGTH = 2000


class Source(StrEnum):
    """Components that assert evidence links on their own behalf."""

    ROUTER = "cortex.router"
    MEMORY_RECALL = "cortex.memory.recall"
    MEMORY_SESSION = "cortex.memory.session"
    KNOWLEDGE_RETRIEVAL = "cortex.knowledge.retrieval"
    KNOWLEDGE_CITATIONS = "cortex.knowledge.citations"
    KNOWLEDGE_INGESTION = "cortex.knowledge.ingestion"


def api_source(api_key_id: uuid.UUID | None) -> str:
    """The source for links recorded through the API, naming the key that sent them."""
    return f"api:key/{api_key_id}" if api_key_id else "api:organization"


class ProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Provenance:
    confidence: float
    explanation: str
    source: str
    timestamp: datetime

    def __post_init__(self) -> None:
        if not (math.isfinite(self.confidence) and 0.0 <= self.confidence <= 1.0):
            raise ProvenanceError(f"confidence must be within [0, 1], got {self.confidence}")
        if not self.explanation.strip():
            raise ProvenanceError("explanation must not be blank")
        if len(self.explanation) > MAX_EXPLANATION_LENGTH:
            raise ProvenanceError(f"explanation exceeds {MAX_EXPLANATION_LENGTH} characters")
        if not self.source.strip():
            raise ProvenanceError("source must not be blank")
        if len(self.source) > MAX_SOURCE_LENGTH:
            raise ProvenanceError(f"source exceeds {MAX_SOURCE_LENGTH} characters")
        if self.timestamp.tzinfo is None:
            raise ProvenanceError("timestamp must be timezone-aware")

    @classmethod
    def now(cls, confidence: float, explanation: str, source: str) -> "Provenance":
        return cls(clamp(confidence), explanation, source, datetime.now(UTC))

    @classmethod
    def of(cls, edge: EvidenceEdge) -> "Provenance":
        return cls(edge.confidence, edge.explanation, edge.source, edge.observed_at)


def clamp(value: float) -> float:
    """Into [0, 1]; NaN counts as no confidence."""
    if math.isnan(value):
        return 0.0
    return min(1.0, max(0.0, value))


def excerpt(text: str, limit: int) -> str:
    """Single-line and at most `limit` characters, for titles and explanations."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def decision_confidence(supports: Iterable[float], contradictions: Iterable[float] = ()) -> float:
    """How well a decision is backed by its direct evidence.

    Each argument is the strength of one direct link: edge confidence times
    the confidence of the evidence node. Supports combine as a noisy-OR, so
    independent evidence accumulates without exceeding 1; each contradiction
    then discounts the result by its own strength. A decision with no
    supporting evidence scores 0: nothing corroborates it.
    """
    doubt = 1.0
    for strength in supports:
        doubt *= 1.0 - clamp(strength)
    confidence = 1.0 - doubt
    for strength in contradictions:
        confidence *= 1.0 - clamp(strength)
    return clamp(confidence)


def occurred_at(node: EvidenceNode) -> datetime:
    """When the underlying record came to be, falling back to when it was recorded."""
    raw = node.metadata_.get("occurred_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return node.created_at
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return node.created_at


_FORWARD = {EvidenceEdgeType.SUPPORTS, EvidenceEdgeType.CONTRADICTS}


def phrase(edge_type: EvidenceEdgeType, upstream: str, downstream: str) -> str:
    """The relationship as a sentence, respecting how each edge type reads."""
    verb = edge_type.value.replace("_", " ")
    if edge_type in _FORWARD:
        return f"{upstream} {verb} {downstream}"
    return f"{downstream} {verb} {upstream}"


class TimelineKind(StrEnum):
    NODE = "node"
    EDGE = "edge"


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    at: datetime
    kind: TimelineKind
    id: uuid.UUID
    label: str
    source: str | None
    confidence: float


def timeline(nodes: Iterable[EvidenceNode], edges: Sequence[EvidenceEdge]) -> list[TimelineEvent]:
    """Every record and every link in the order it happened.

    Records are placed at `occurred_at` (a document's ingestion, a message's
    send time), links at the moment their relationship was observed.
    """
    titles: dict[uuid.UUID, str] = {}
    events: list[TimelineEvent] = []
    for node in nodes:
        titles[node.id] = node.title
        events.append(
            TimelineEvent(
                at=occurred_at(node),
                kind=TimelineKind.NODE,
                id=node.id,
                label=f"{node.type}: {node.title}",
                source=None,
                confidence=node.confidence,
            )
        )
    for edge in edges:
        upstream = titles.get(edge.from_node_id, str(edge.from_node_id))
        downstream = titles.get(edge.to_node_id, str(edge.to_node_id))
        events.append(
            TimelineEvent(
                at=edge.observed_at,
                kind=TimelineKind.EDGE,
                id=edge.id,
                label=phrase(edge.type, upstream, downstream),
                source=edge.source,
                confidence=edge.confidence,
            )
        )
    # Records before the links that mention them when timestamps tie.
    events.sort(key=lambda e: (e.at, e.kind is TimelineKind.EDGE, str(e.id)))
    return events
