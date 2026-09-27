"""Request parameters and response models.

Parameters are `TypedDict`s, so plain dicts work and type checkers catch
typos; the SDK validates them before sending. Responses are Pydantic models
that tolerate additive API changes: unknown fields are kept (`model_extra`)
and enum-like fields are plain strings, so a new provider or finish reason
never breaks an existing client. Mirrors `cortex_api.schemas`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, NotRequired, Required, TypedDict

from pydantic import BaseModel, ConfigDict, Field, with_config

Role = Literal["system", "user", "assistant", "tool"]
MemoryType = Literal["episodic", "semantic", "procedural", "preference"]
SearchMode = Literal["hybrid", "vector", "keyword"]
Objective = Literal["balanced", "quality", "speed", "cost"]
RoutingMode = Literal["auto", "preferred", "strict"]
ProviderName = Literal["openai", "anthropic", "gemini", "deepseek", "qwen", "llama"]
Capability = Literal["chat", "vision", "tools", "json_mode", "reasoning"]
TextMimeType = Literal["text/plain", "text/markdown"]

Metadata = dict[str, Any]

# --- Parameters ----------------------------------------------------------------------------------

_loose = ConfigDict(extra="allow")


@with_config(_loose)
class ChatMessageParam(TypedDict):
    role: Role
    content: Annotated[str, Field(min_length=1, max_length=100_000)]


@with_config(_loose)
class MemoryOptions(TypedDict, total=False):
    conversation_id: Required[str]
    include_history: bool
    """Prepend the conversation's hot session; send only new turns. Default true."""
    memory_limit: Annotated[int, Field(ge=0, le=20)]
    """Long-term memories to inject. Default 5."""
    persist: bool
    """Append the new turns and the output to the conversation. Default true."""


@with_config(_loose)
class SearchFilters(TypedDict, total=False):
    document_ids: Annotated[list[str], Field(max_length=100)]
    sources: Annotated[list[str], Field(max_length=50)]
    mime_types: Annotated[list[str], Field(max_length=10)]
    metadata: Metadata
    """Containment: `{"team": "billing"}` keeps documents whose metadata includes it."""
    created_after: datetime | str
    created_before: datetime | str


@with_config(_loose)
class KnowledgeOptions(TypedDict, total=False):
    top_k: Annotated[int, Field(ge=1, le=50)]
    mode: SearchMode
    filters: SearchFilters
    max_context_tokens: Annotated[int, Field(ge=100, le=200_000)]
    min_confidence: Annotated[float, Field(ge=0, le=1)]
    """Below this, the context is withheld from the model."""
    query: Annotated[str, Field(max_length=2000)]
    """Defaults to the last user message."""


@with_config(_loose)
class RoutingOptions(TypedDict, total=False):
    mode: RoutingMode
    """Defaults to `preferred` when a model or provider is given, else `auto`."""
    provider: ProviderName
    capabilities: list[Capability]
    allow_fallback: bool


@with_config(_loose)
class ChunkingOptions(TypedDict, total=False):
    chunk_size: Annotated[int, Field(ge=32, le=8192)]
    chunk_overlap: Annotated[int, Field(ge=0, le=2048)]
    separators: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=16)]], Field(min_length=1, max_length=20)
    ]


@with_config(_loose)
class CompletionParams(TypedDict):
    messages: Annotated[list[ChatMessageParam], Field(min_length=1, max_length=500)]
    model: NotRequired[Annotated[str, Field(min_length=1, max_length=128)]]
    objective: NotRequired[Objective]
    temperature: NotRequired[Annotated[float, Field(ge=0, le=2)]]
    max_tokens: NotRequired[Annotated[int, Field(ge=1, le=200_000)]]
    memory: NotRequired[MemoryOptions]
    knowledge: NotRequired[KnowledgeOptions]
    metadata: NotRequired[Metadata]
    routing: NotRequired[RoutingOptions]


@with_config(_loose)
class ConversationParams(TypedDict, total=False):
    title: Annotated[str, Field(max_length=255)] | None


@with_config(_loose)
class MessageParams(TypedDict):
    conversation_id: Annotated[str, Field(min_length=1)]
    role: Role
    content: Annotated[str, Field(min_length=1, max_length=100_000)]
    metadata: NotRequired[Metadata]


@with_config(_loose)
class MemoryParams(TypedDict):
    type: MemoryType
    content: Annotated[str, Field(min_length=1, max_length=20_000)]
    summary: NotRequired[Annotated[str, Field(max_length=1000)] | None]
    importance: NotRequired[Annotated[float, Field(ge=0, le=1)]]
    source_message_id: NotRequired[str | None]


@with_config(_loose)
class SearchParams(TypedDict):
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    top_k: NotRequired[Annotated[int, Field(ge=1, le=50)]]
    mode: NotRequired[SearchMode]
    filters: NotRequired[SearchFilters]
    recency_weight: NotRequired[Annotated[float, Field(ge=0, le=1)]]
    max_context_tokens: NotRequired[Annotated[int, Field(ge=100, le=200_000)]]


@with_config(_loose)
class DocumentParams(TypedDict):
    title: Annotated[str, Field(min_length=1, max_length=512)]
    content: Annotated[str, Field(min_length=1)]
    source: NotRequired[Annotated[str, Field(max_length=2048)]]
    mime_type: NotRequired[TextMimeType]
    metadata: NotRequired[Metadata]
    chunking: NotRequired[ChunkingOptions]


@with_config(_loose)
class IngestParams(TypedDict, total=False):
    title: Annotated[str, Field(min_length=1, max_length=512)]
    source: Annotated[str, Field(max_length=2048)]
    metadata: Metadata
    chunking: ChunkingOptions


EvidenceNodeType = Literal[
    "decision", "memory", "message", "conversation", "document", "chunk", "knowledge", "benchmark"
]
EvidenceEdgeType = Literal[
    "supports", "references", "derived_from", "retrieved_from", "generated_by", "contradicts"
]
Unit = Annotated[float, Field(ge=0, le=1)]


@with_config(_loose)
class EvidenceParams(TypedDict, total=False):
    """One piece of evidence behind a recorded decision."""

    type: Required[EvidenceNodeType]
    ref_id: str | None
    """Evidence with the same type and ref_id is shared across decisions."""
    title: Required[Annotated[str, Field(min_length=1, max_length=500)]]
    confidence: Unit
    """How reliable the evidence is. Default 1."""
    metadata: Metadata
    relation: EvidenceEdgeType
    """Default `supports`."""
    relation_confidence: Unit
    """How strongly the evidence bears on the decision. Default 1."""
    explanation: Required[Annotated[str, Field(min_length=1, max_length=2000)]]
    observed_at: datetime | None


@with_config(_loose)
class DecisionParams(TypedDict, total=False):
    ref_id: str | None
    title: Required[Annotated[str, Field(min_length=1, max_length=500)]]
    confidence: Unit | None
    metadata: Metadata
    source: Annotated[str, Field(min_length=1, max_length=191)] | None
    evidence: Required[Annotated[list[EvidenceParams], Field(min_length=1, max_length=200)]]


# --- Responses -----------------------------------------------------------------------------------


class CortexModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class Page[T](CortexModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


# Chat


class TokenUsage(CortexModel):
    prompt: int
    completion: int
    total: int


class CompletionAttempt(CortexModel):
    provider: str
    model: str
    success: bool
    latency_ms: float
    error: str | None = None


class Confidence(CortexModel):
    score: float
    level: str
    """`high`, `medium`, `low`, or `none`."""
    similarity: float | None = None
    coverage: float | None = None
    agreement: float | None = None


class Citation(CortexModel):
    index: int
    document_id: str
    chunk_ids: list[str] = []
    title: str
    source: str | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    label: str
    """`Title > Section (p. 3)`"""
    score: float
    snippet: str
    cited: bool | None = None
    """On completions: whether the answer referenced this citation."""


class KnowledgeUsage(CortexModel):
    query_id: str
    applied: bool
    """False when nothing was retrieved or confidence was below `min_confidence`."""
    confidence: Confidence
    citations: list[Citation]
    cited: list[int]
    """Citation numbers the answer referenced, in order of first use."""


class Completion(CortexModel):
    id: str
    object: Literal["cortex.completion"]
    created_at: datetime
    provider: str
    model: str
    output: str
    latency_ms: float
    tokens: TokenUsage
    finish_reason: str
    routing_reason: str
    routing_mode: str
    cost_estimate: float
    """USD, from catalog prices."""
    attempts: list[CompletionAttempt]
    conversation_id: str | None = None
    knowledge: KnowledgeUsage | None = None


# Memory


class Conversation(CortexModel):
    id: str
    organization_id: str
    title: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationSummary(Conversation):
    message_count: int
    session: str | None = None
    """`hot` when a Redis session exists, `cold` otherwise, None when Redis was unreachable."""


class Message(CortexModel):
    id: str
    conversation_id: str
    role: str
    content: str
    token_count: int
    metadata: Metadata = {}
    created_at: datetime


class ConversationDetail(Conversation):
    message_count: int
    messages: list[Message]
    """Most recent messages, oldest first."""


class SessionMessage(CortexModel):
    id: str
    role: str
    content: str
    token_count: int
    created_at: datetime


class Memory(CortexModel):
    id: str
    organization_id: str
    type: str
    summary: str
    content: str
    importance: float
    source_message_id: str | None = None
    created_at: datetime
    updated_at: datetime


class RankedMemory(Memory):
    score: float
    """Relevance x importance x recency. Comparable within one response only."""


class MemorySearchResult(CortexModel):
    query: str | None = None
    memories: list[RankedMemory]


class ConversationContext(CortexModel):
    conversation_id: str
    messages: list[SessionMessage]
    token_count: int
    last_activity: datetime
    source: str
    """`cache` (hot Redis session) or `database`."""
    memories: list[RankedMemory]


# Knowledge


class IngestionStats(CortexModel):
    ingestion_ms: float
    embedding_ms: float = 0
    stages: Metadata = {}


class Document(CortexModel):
    id: str
    title: str
    source: str | None = None
    mime_type: str
    metadata: Metadata = {}
    content_hash: str
    byte_size: int
    char_count: int
    token_count: int
    chunk_count: int
    embedding_space: str | None = None
    """`provider/model@dims`; None when nothing could be embedded."""
    ingestion: IngestionStats
    created_at: datetime


class DocumentChunk(CortexModel):
    id: str
    chunk_index: int
    content: str
    token_count: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    char_start: int
    char_end: int
    embedding_space: str | None = None


class DocumentDetail(Document):
    chunks: list[DocumentChunk] | None = None
    """Present when requested with `include_chunks=True`."""


class SearchHit(CortexModel):
    chunk_id: str
    document_id: str
    title: str
    source: str | None = None
    section: str | None = None
    chunk_index: int
    page_start: int | None = None
    page_end: int | None = None
    content: str
    score: float
    vector_similarity: float | None = None
    keyword_score: float | None = None
    vector_rank: int | None = None
    keyword_rank: int | None = None
    recency: float
    citation: int | None = None
    """Citation number when the chunk made it into the context."""


class AssembledContext(CortexModel):
    text: str
    """Numbered sources, ready for a system message."""
    token_count: int
    truncated: bool
    citations: list[Citation]
    confidence: Confidence


class SearchMetrics(CortexModel):
    latency_ms: float
    embedding_ms: float = 0
    vector_ms: float = 0
    keyword_ms: float = 0
    vector_candidates: int = 0
    keyword_candidates: int = 0
    query_embedding_cached: bool = False
    embedding_space: str | None = None
    query_terms: list[str] = []
    warnings: list[str] = []


class SearchResult(CortexModel):
    query_id: str
    query: str
    mode: str
    results: list[SearchHit]
    context: AssembledContext
    metrics: SearchMetrics


class CorpusMetrics(CortexModel):
    documents: int
    chunks: int
    tokens: int
    ingested: int
    avg_ingestion_ms: float | None = None
    p95_ingestion_ms: float | None = None
    avg_embedding_ms: float | None = None


class RetrievalMetrics(CortexModel):
    queries: int
    p50_ms: float | None = None
    p95_ms: float | None = None
    avg_embedding_ms: float | None = None
    avg_confidence: float | None = None
    avg_coverage: float | None = None
    avg_agreement: float | None = None
    zero_result_rate: float | None = None
    cache_hit_rate: float | None = None
    grounded_completions: int = 0
    citations_offered: int = 0
    citations_used: int = 0
    citation_usage_rate: float | None = None


class KnowledgeMetrics(CortexModel):
    window_hours: int
    corpus: CorpusMetrics
    retrieval: RetrievalMetrics


class KnowledgeQuery(CortexModel):
    id: str
    query: str
    mode: str
    top_k: int
    filters: Metadata = {}
    embedding_space: str | None = None
    result_count: int
    citation_count: int
    context_tokens: int
    latency_ms: float
    confidence: float
    completion_id: str | None = None
    """Set when the retrieval grounded a completion."""
    cited: list[int] | None = None
    created_at: datetime


class KnowledgeQueryResult(CortexModel):
    chunk_id: str
    document_id: str
    score: float
    citation: int | None = None


class KnowledgeQueryDetail(KnowledgeQuery):
    results: list[KnowledgeQueryResult]


# Router


class Pricing(CortexModel):
    input_per_mtok: float
    output_per_mtok: float
    currency: str = "USD"


class ModelInfo(CortexModel):
    id: str
    """`provider/model`; accepted as `model` in completions."""
    provider: str
    model: str
    display_name: str
    capabilities: list[str]
    context_window: int
    max_output_tokens: int
    pricing: Pricing
    quality: float
    expected_latency_ms: float
    available: bool
    availability: str
    """`ok`, `not_configured`, or `circuit_open`."""
    allowed: bool
    """Permitted by this organization's routing policy."""


class ModelList(CortexModel):
    models: list[ModelInfo]


class Execution(CortexModel):
    """One provider call. A completion with fallbacks has several."""

    id: str
    organization_id: str
    completion_id: str
    attempt: int
    provider: str
    model: str
    routing_mode: str
    is_fallback: bool
    latency_ms: float
    prompt_tokens: int
    completion_tokens: int
    cost_estimate: float
    success: bool
    finish_reason: str | None = None
    error_type: str | None = None
    error: str | None = None
    metadata: Metadata = {}
    created_at: datetime


# System


class Organization(CortexModel):
    id: str
    name: str
    slug: str
    settings: Metadata = {}
    created_at: datetime
    updated_at: datetime


class ApiKey(CortexModel):
    """An organization credential. The secret itself is never listed."""

    id: str
    organization_id: str
    name: str
    prefix: str | None = None
    """Leading characters of the secret, safe to display."""
    role: str
    """`admin` (can also manage API keys) or `member`."""
    status: str
    """`active`, `expired`, or `revoked`."""
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    rotated_from_id: str | None = None


class ApiKeyWithSecret(ApiKey):
    """Returned once, by create and rotate. Store `secret` immediately."""

    secret: str


class ApiKeyList(CortexModel):
    items: list[ApiKey]


class Health(CortexModel):
    status: str
    service: str
    version: str


class DependencyCheck(CortexModel):
    status: str
    """`up` or `down`."""
    latency_ms: float | None = None
    error: str | None = None


class Readiness(CortexModel):
    status: str
    """`healthy` or `degraded`. The API answers 503 when degraded; the SDK returns it anyway."""
    service: str
    version: str
    checks: dict[str, DependencyCheck]


class DatabaseHealth(CortexModel):
    status: str
    database: str


class RedisHealth(CortexModel):
    status: str
    redis: str
    latency_ms: float | None = None


class ProviderHealth(CortexModel):
    name: str
    status: str
    """`available`, `circuit_open`, or `unconfigured`."""
    configured: bool
    circuit_open: bool
    consecutive_failures: int
    last_error: str | None = None


class EmbeddingHealth(CortexModel):
    space: str
    configured: bool


class ProvidersHealth(CortexModel):
    status: str
    """`healthy`, `degraded` (some providers cannot take traffic), or `unhealthy` (none can)."""
    available: int
    providers: list[ProviderHealth]
    embeddings: EmbeddingHealth


# Evidence


class EvidenceNode(CortexModel):
    id: str
    type: str
    ref_id: str | None
    """The record this node stands for: for completion decisions, the completion id."""
    title: str
    confidence: float
    created_at: datetime
    occurred_at: datetime
    metadata: Metadata


class Provenance(CortexModel):
    confidence: float
    explanation: str
    source: str
    """The component (`cortex.*`) or API key that asserted the link."""
    timestamp: datetime


class EvidenceEdge(CortexModel):
    id: str
    type: str
    from_node_id: str
    """Upstream: the evidence."""
    to_node_id: str
    """Downstream: what the evidence informed."""
    provenance: Provenance
    created_at: datetime


class SupportingEvidence(CortexModel):
    node: EvidenceNode
    depth: int
    path_confidence: float
    strength: float
    path: list[str]


class Contradiction(CortexModel):
    node: EvidenceNode
    edge: EvidenceEdge


class DecisionEvidence(CortexModel):
    decision: EvidenceNode
    supporting: list[SupportingEvidence]
    contradicting: list[Contradiction]
    counts: dict[str, int]


class EvidenceGraphNode(EvidenceNode):
    depth: int
    """Signed hops from the root: negative upstream (evidence), positive downstream."""


class EvidenceTimelineEvent(CortexModel):
    at: datetime
    kind: str
    id: str
    label: str
    source: str | None
    confidence: float


class EvidenceGraph(CortexModel):
    root_id: str
    depth: int
    nodes: list[EvidenceGraphNode]
    edges: list[EvidenceEdge]
    timeline: list[EvidenceTimelineEvent]
    truncated: bool


class EvidenceNeighbor(CortexModel):
    edge: EvidenceEdge
    node: EvidenceNode


class ReachedDecision(CortexModel):
    node: EvidenceNode
    depth: int


class EvidenceNodeDetail(CortexModel):
    node: EvidenceNode
    upstream: list[EvidenceNeighbor]
    downstream: list[EvidenceNeighbor]
    decisions: list[ReachedDecision]


class EvidencePath(CortexModel):
    source_id: str
    target_id: str
    connected: bool
    edges: list[EvidenceEdge]
    nodes: list[EvidenceNode]
