"""Recording decisions and linking them to the evidence that produced them."""

import uuid
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.models.memory import Memory
from cortex_api.models.message import Message
from cortex_api.repositories.evidence_repository import (
    EdgeValues,
    EvidenceRepository,
    NodeValues,
)
from cortex_api.schemas.memory import SessionMessage
from cortex_api.services.evidence.provenance import (
    Provenance,
    Source,
    clamp,
    decision_confidence,
    excerpt,
)
from cortex_api.services.knowledge.service import Retrieval
from cortex_api.services.memory.retrieval import RankedMemory

TITLE_LIMIT = 200


class DecisionExistsError(ValueError):
    def __init__(self, decision_id: uuid.UUID) -> None:
        super().__init__(f"Decision {decision_id} is already recorded")
        self.decision_id = decision_id


@dataclass(frozen=True, slots=True)
class Link:
    upstream: Hashable
    downstream: Hashable
    type: EvidenceEdgeType
    provenance: Provenance


@dataclass(slots=True)
class EvidencePlan:
    """Nodes and links to write in one batch, addressed by caller-chosen keys.

    Referenced records are keyed by (type, ref_id), so adding the same memory
    twice yields one node. The first link between two keys with a given type
    wins, matching how the repository treats edges already stored.
    """

    nodes: dict[Hashable, NodeValues] = field(default_factory=dict)
    links: dict[tuple[Hashable, Hashable, EvidenceEdgeType], Link] = field(default_factory=dict)

    def add(self, values: NodeValues) -> Hashable:
        key: Hashable = (values.type, values.ref_id) if values.ref_id else uuid7()
        self.nodes.setdefault(key, values)
        return key

    def link(
        self,
        upstream: Hashable,
        downstream: Hashable,
        type_: EvidenceEdgeType,
        provenance: Provenance,
    ) -> None:
        if upstream == downstream:
            raise ValueError("a node cannot be evidence for itself")
        self.links.setdefault(
            (upstream, downstream, type_), Link(upstream, downstream, type_, provenance)
        )

    def strengths(self, key: Hashable, type_: EvidenceEdgeType) -> list[float]:
        """Strength of each direct `type_` link into `key`: edge times node confidence."""
        return [
            link.provenance.confidence * self.nodes[link.upstream].confidence
            for link in self.links.values()
            if link.downstream == key and link.type is type_
        ]


@dataclass(frozen=True, slots=True)
class RecordedDecision:
    decision: EvidenceNode
    nodes: list[EvidenceNode]
    edges: list[EvidenceEdge]


@dataclass(frozen=True, slots=True)
class CompletionTrail:
    """Everything a completion was built from, as the completion service saw it."""

    completion_id: uuid.UUID
    created_at: datetime
    prompt: str
    provider: str
    model: str
    objective: str
    routing_mode: str
    routing_reason: str
    execution_id: uuid.UUID
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_estimate: float
    attempts: int
    conversation_id: uuid.UUID | None = None
    history: Sequence[SessionMessage] = ()
    memories: Sequence[RankedMemory] = ()
    retrieval: Retrieval | None = None
    grounding_applied: bool = False
    cited: Sequence[int] = ()


class EvidenceLinker:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = EvidenceRepository(session)

    async def write(
        self, organization_id: uuid.UUID, plan: EvidencePlan
    ) -> dict[Hashable, EvidenceNode]:
        keys = list(plan.nodes)
        nodes = await self.repository.upsert_nodes(organization_id, [plan.nodes[k] for k in keys])
        by_key = dict(zip(keys, nodes, strict=True))
        await self.repository.upsert_edges(
            organization_id,
            [
                EdgeValues(
                    from_node_id=by_key[link.upstream].id,
                    to_node_id=by_key[link.downstream].id,
                    type=link.type,
                    confidence=link.provenance.confidence,
                    explanation=link.provenance.explanation,
                    source=link.provenance.source,
                    observed_at=link.provenance.timestamp,
                )
                for link in plan.links.values()
            ],
        )
        return by_key

    async def link(
        self,
        organization_id: uuid.UUID,
        upstream: EvidenceNode,
        downstream: EvidenceNode,
        type_: EvidenceEdgeType,
        provenance: Provenance,
    ) -> EvidenceEdge:
        """One edge between stored nodes; an existing edge keeps its provenance."""
        if upstream.id == downstream.id:
            raise ValueError("a node cannot be evidence for itself")
        [edge] = await self.repository.upsert_edges(
            organization_id,
            [
                EdgeValues(
                    from_node_id=upstream.id,
                    to_node_id=downstream.id,
                    type=type_,
                    confidence=provenance.confidence,
                    explanation=provenance.explanation,
                    source=provenance.source,
                    observed_at=provenance.timestamp,
                )
            ],
        )
        return edge

    async def record_decision(
        self,
        organization_id: uuid.UUID,
        plan: EvidencePlan,
        decision_key: Hashable,
        *,
        confidence: float | None = None,
    ) -> RecordedDecision:
        """Writes a new decision and its evidence.

        Without an explicit `confidence`, the decision's confidence is derived
        from its direct supporting and contradicting links.
        """
        values = plan.nodes[decision_key]
        if values.type is not EvidenceNodeType.DECISION or values.ref_id is None:
            raise ValueError("decision_key must name a decision node with a ref_id")
        if await self.repository.get_by_ref(organization_id, values.type, values.ref_id):
            raise DecisionExistsError(values.ref_id)
        if confidence is None:
            confidence = decision_confidence(
                plan.strengths(decision_key, EvidenceEdgeType.SUPPORTS),
                plan.strengths(decision_key, EvidenceEdgeType.CONTRADICTS),
            )
        plan.nodes[decision_key] = NodeValues(
            type=values.type,
            ref_id=values.ref_id,
            title=values.title,
            confidence=clamp(confidence),
            metadata=values.metadata,
        )
        by_key = await self.write(organization_id, plan)
        edges = await self.repository.edges_touching(
            organization_id, [n.id for n in by_key.values()]
        )
        node_ids = {n.id for n in by_key.values()}
        return RecordedDecision(
            decision=by_key[decision_key],
            nodes=list(by_key.values()),
            edges=[e for e in edges if e.from_node_id in node_ids and e.to_node_id in node_ids],
        )

    async def record_completion(
        self, organization_id: uuid.UUID, trail: CompletionTrail
    ) -> RecordedDecision:
        plan = EvidencePlan()
        decision = plan.add(
            NodeValues(
                type=EvidenceNodeType.DECISION,
                ref_id=trail.completion_id,
                title=f"Answer: {excerpt(trail.prompt, TITLE_LIMIT - 8)}",
                confidence=0.0,
                metadata={
                    "kind": "completion",
                    "occurred_at": trail.created_at.isoformat(),
                    "provider": trail.provider,
                    "model": trail.model,
                    "objective": trail.objective,
                    "routing_mode": trail.routing_mode,
                },
            )
        )
        _plan_generation(plan, decision, trail)
        _plan_memories(plan, decision, trail.memories)
        if trail.conversation_id is not None:
            _plan_conversation(plan, decision, trail.conversation_id, trail.history)
        if trail.retrieval is not None:
            _plan_retrieval(plan, decision, trail.retrieval, trail.grounding_applied, trail.cited)
        return await self.record_decision(organization_id, plan, decision)

    async def link_conversation_turns(
        self,
        organization_id: uuid.UUID,
        completion_id: uuid.UUID,
        prompts: Sequence[Message],
        reply: Message | None,
    ) -> None:
        """Links the persisted turns of a completion: the prompts it answered, and its reply."""
        existing = await self.repository.get_by_ref(
            organization_id, EvidenceNodeType.DECISION, completion_id
        )
        if existing is None:
            return
        plan = EvidencePlan()
        decision = plan.add(
            NodeValues(
                type=existing.type,
                ref_id=existing.ref_id,
                title=existing.title,
                confidence=existing.confidence,
            )
        )
        for message in prompts:
            key = plan.add(
                _message_values(
                    message.id,
                    message.role,
                    message.content,
                    message.token_count,
                    message.conversation_id,
                    message.created_at,
                )
            )
            plan.link(
                key,
                decision,
                EvidenceEdgeType.DERIVED_FROM,
                Provenance.now(1.0, "The prompt this completion answered", Source.MEMORY_SESSION),
            )
        if reply is not None:
            key = plan.add(
                _message_values(
                    reply.id,
                    reply.role,
                    reply.content,
                    reply.token_count,
                    reply.conversation_id,
                    reply.created_at,
                )
            )
            plan.link(
                decision,
                key,
                EvidenceEdgeType.GENERATED_BY,
                Provenance.now(
                    1.0, "Stored as the assistant reply in the conversation", Source.ROUTER
                ),
            )
        await self.write(organization_id, plan)


def _message_values(
    message_id: uuid.UUID,
    role: str,
    content: str,
    token_count: int,
    conversation_id: uuid.UUID | None,
    created_at: datetime,
) -> NodeValues:
    metadata: dict[str, Any] = {
        "role": str(role),
        "token_count": token_count,
        "excerpt": excerpt(content, 280),
        "occurred_at": created_at.isoformat(),
    }
    if conversation_id is not None:
        metadata["conversation_id"] = str(conversation_id)
    return NodeValues(
        type=EvidenceNodeType.MESSAGE,
        ref_id=message_id,
        title=f"{str(role).capitalize()}: {excerpt(content, TITLE_LIMIT - 12)}",
        confidence=1.0,
        metadata=metadata,
    )


def _plan_generation(plan: EvidencePlan, decision: Hashable, trail: CompletionTrail) -> None:
    benchmark = plan.add(
        NodeValues(
            type=EvidenceNodeType.BENCHMARK,
            ref_id=trail.execution_id,
            title=f"{trail.provider}/{trail.model}",
            confidence=1.0,
            metadata={
                "provider": trail.provider,
                "model": trail.model,
                "latency_ms": trail.latency_ms,
                "prompt_tokens": trail.prompt_tokens,
                "completion_tokens": trail.completion_tokens,
                "cost_estimate": trail.cost_estimate,
                "attempts": trail.attempts,
                "occurred_at": trail.created_at.isoformat(),
            },
        )
    )
    plan.link(
        benchmark,
        decision,
        EvidenceEdgeType.GENERATED_BY,
        Provenance.now(
            1.0,
            f"Generated by {trail.provider}/{trail.model}. {trail.routing_reason}",
            Source.ROUTER,
        ),
    )


def _memory_values(memory: Memory) -> NodeValues:
    return NodeValues(
        type=EvidenceNodeType.MEMORY,
        ref_id=memory.id,
        title=excerpt(memory.summary, TITLE_LIMIT),
        confidence=1.0,
        metadata={
            "memory_type": str(memory.type),
            "importance": memory.importance,
            "occurred_at": memory.created_at.isoformat(),
        },
    )


def _plan_memories(
    plan: EvidencePlan, decision: Hashable, memories: Sequence[RankedMemory]
) -> None:
    for rank, ranked in enumerate(memories, start=1):
        key = plan.add(_memory_values(ranked.memory))
        plan.link(
            key,
            decision,
            EvidenceEdgeType.SUPPORTS,
            Provenance.now(
                ranked.score,
                f"Recalled from long-term memory as #{rank} of {len(memories)} "
                f"(relevance {ranked.score:.2f})",
                Source.MEMORY_RECALL,
            ),
        )


def _plan_conversation(
    plan: EvidencePlan,
    decision: Hashable,
    conversation_id: uuid.UUID,
    history: Sequence[SessionMessage],
) -> None:
    conversation = plan.add(
        NodeValues(
            type=EvidenceNodeType.CONVERSATION,
            ref_id=conversation_id,
            title=f"Conversation {str(conversation_id)[:8]}",
            confidence=1.0,
        )
    )
    included = f"{len(history)} earlier message{'s' if len(history) != 1 else ''}"
    plan.link(
        conversation,
        decision,
        EvidenceEdgeType.REFERENCES,
        Provenance.now(
            1.0, f"Answered within this conversation; {included} included", Source.MEMORY_SESSION
        ),
    )
    for position, message in enumerate(history, start=1):
        key = plan.add(
            _message_values(
                message.id,
                message.role,
                message.content,
                message.token_count,
                conversation_id,
                message.created_at,
            )
        )
        plan.link(
            key,
            decision,
            EvidenceEdgeType.DERIVED_FROM,
            Provenance.now(
                1.0,
                f"Included as conversation history ({position} of {len(history)})",
                Source.MEMORY_SESSION,
            ),
        )


def _plan_retrieval(
    plan: EvidencePlan,
    decision: Hashable,
    retrieval: Retrieval,
    applied: bool,
    cited: Sequence[int],
) -> None:
    context = retrieval.context
    confidence = context.confidence
    hits = context.search.hits
    knowledge = plan.add(
        NodeValues(
            type=EvidenceNodeType.KNOWLEDGE,
            ref_id=retrieval.query_id,
            title=f'Retrieval: "{excerpt(context.query, TITLE_LIMIT - 14)}"',
            confidence=clamp(confidence.score),
            metadata={
                "mode": str(context.search.stats.mode),
                "level": str(confidence.level),
                "results": len(hits),
                "citations": len(context.citations),
                "occurred_at": datetime.now(UTC).isoformat(),
            },
        )
    )
    if applied:
        relation = EvidenceEdgeType.SUPPORTS
        explanation = (
            f"Grounded the answer with {len(context.citations)} numbered sources "
            f"({confidence.level} confidence, {confidence.score:.2f})"
        )
    else:
        relation = EvidenceEdgeType.REFERENCES
        explanation = "Retrieved but not shown to the model: " + (
            f"confidence {confidence.score:.2f} was below the requested minimum"
            if context.citations
            else "no passages matched"
        )
    plan.link(
        knowledge,
        decision,
        relation,
        Provenance.now(confidence.score, explanation, Source.KNOWLEDGE_RETRIEVAL),
    )

    # Fused scores are only comparable within one result set, so rank-relative
    # strength (top hit = 1) is what the edge records; the raw score is explained.
    top = max((hit.score for hit in hits), default=0.0)
    chunks: dict[uuid.UUID, Hashable] = {}
    for rank, hit in enumerate(hits, start=1):
        chunk, document = hit.chunk, hit.document
        location = chunk.section or f"chunk {chunk.chunk_index + 1}"
        chunk_key = plan.add(
            NodeValues(
                type=EvidenceNodeType.CHUNK,
                ref_id=chunk.id,
                title=excerpt(f"{document.title} · {location}", TITLE_LIMIT),
                confidence=1.0,
                metadata={
                    "document_id": str(document.id),
                    "chunk_index": chunk.chunk_index,
                    "section": chunk.section,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "excerpt": excerpt(chunk.content, 280),
                    "occurred_at": chunk.created_at.isoformat(),
                },
            )
        )
        chunks[chunk.id] = chunk_key
        document_key = plan.add(
            NodeValues(
                type=EvidenceNodeType.DOCUMENT,
                ref_id=document.id,
                title=excerpt(document.title, TITLE_LIMIT),
                confidence=1.0,
                metadata={
                    "source": document.source,
                    "mime_type": document.mime_type,
                    "chunk_count": document.chunk_count,
                    "occurred_at": document.created_at.isoformat(),
                },
            )
        )
        plan.link(
            document_key,
            chunk_key,
            EvidenceEdgeType.DERIVED_FROM,
            Provenance.now(
                1.0,
                f"Chunk {chunk.chunk_index + 1} of {document.chunk_count} in this document",
                Source.KNOWLEDGE_INGESTION,
            ),
        )
        plan.link(
            chunk_key,
            knowledge,
            EvidenceEdgeType.RETRIEVED_FROM,
            Provenance.now(
                hit.score / top if top > 0 else 0.0,
                f"Ranked #{rank} of {len(hits)} by {context.search.stats.mode} search "
                f"(score {hit.score:.4f})",
                Source.KNOWLEDGE_RETRIEVAL,
            ),
        )

    if not applied:
        return
    top_citation = max((c.score for c in context.citations), default=0.0)
    for index in cited:
        if not 1 <= index <= len(context.citations):
            continue
        citation = context.citations[index - 1]
        for chunk_id in citation.chunk_ids:
            chunk_key = chunks.get(chunk_id)
            if chunk_key is None:
                continue
            plan.link(
                chunk_key,
                decision,
                EvidenceEdgeType.SUPPORTS,
                Provenance.now(
                    citation.score / top_citation if top_citation > 0 else 0.0,
                    f"Cited as [{index}] in the answer",
                    Source.KNOWLEDGE_CITATIONS,
                ),
            )
