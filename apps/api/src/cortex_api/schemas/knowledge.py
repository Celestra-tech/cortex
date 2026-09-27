import json
import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.knowledge_query import SearchMode
from cortex_api.repositories.knowledge_repository import DocumentFilters
from cortex_api.services.knowledge.assembler import AssembledContext, ConfidenceLevel
from cortex_api.services.knowledge.chunker import ChunkingConfig
from cortex_api.services.knowledge.citations import Citation
from cortex_api.services.knowledge.hybrid_search import SearchHit

MAX_METADATA_BYTES = 16_384


def check_metadata_size(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
        raise ValueError(f"metadata must serialize to at most {MAX_METADATA_BYTES} bytes")
    return value


# --- Ingestion ------------------------------------------------------------------------------------


class ChunkingOptions(BaseModel):
    """Per-document overrides of the deployment's chunking defaults."""

    model_config = ConfigDict(extra="forbid")

    chunk_size: int | None = Field(default=None, ge=32, le=8192)
    chunk_overlap: int | None = Field(default=None, ge=0, le=2048)
    separators: list[str] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("separators")
    @classmethod
    def _separators_non_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not s or len(s) > 16 for s in value):
            raise ValueError("separators must be 1-16 characters each")
        return value

    @model_validator(mode="after")
    def _overlap_below_size(self) -> Self:
        if (
            self.chunk_size is not None
            and self.chunk_overlap is not None
            and self.chunk_overlap >= self.chunk_size
        ):
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        return self

    def resolve(self, defaults: ChunkingConfig) -> ChunkingConfig:
        """Raises ValueError when an explicit overlap does not fit the default size."""
        size = self.chunk_size or defaults.chunk_size
        overlap = (
            min(defaults.chunk_overlap, size // 4)
            if self.chunk_overlap is None
            else self.chunk_overlap
        )
        return ChunkingConfig(
            chunk_size=size,
            chunk_overlap=overlap,
            separators=tuple(self.separators) if self.separators else defaults.separators,
            min_chunk_tokens=defaults.min_chunk_tokens,
        )


class DocumentCreate(BaseModel):
    """Text submitted inline. Binary formats (PDF, DOCX) go through `/documents/ingest`."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    source: str | None = Field(default=None, max_length=2048)
    mime_type: Literal["text/plain", "text/markdown"] = "text/plain"
    metadata: dict[str, Any] = Field(default_factory=dict)
    chunking: ChunkingOptions | None = None

    _metadata_size = field_validator("metadata")(check_metadata_size)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class IngestionStats(BaseModel):
    ingestion_ms: int
    embedding_ms: int
    stages: dict[str, Any]


class DocumentRead(BaseModel):
    id: uuid.UUID
    title: str
    source: str | None
    mime_type: str
    metadata: dict[str, Any]
    content_hash: str
    byte_size: int
    char_count: int
    token_count: int
    chunk_count: int
    embedding_space: str | None
    ingestion: IngestionStats
    created_at: datetime

    @classmethod
    def from_document(cls, document: Document) -> Self:
        return cls(
            id=document.id,
            title=document.title,
            source=document.source,
            mime_type=document.mime_type,
            metadata=document.metadata_,
            content_hash=document.content_hash,
            byte_size=document.byte_size,
            char_count=document.char_count,
            token_count=document.token_count,
            chunk_count=document.chunk_count,
            embedding_space=document.embedding_space,
            ingestion=IngestionStats(
                ingestion_ms=document.ingestion_ms,
                embedding_ms=document.embedding_ms,
                stages=document.ingestion_stats,
            ),
            created_at=document.created_at,
        )


class ChunkRead(BaseModel):
    id: uuid.UUID
    chunk_index: int
    content: str
    token_count: int
    section: str | None
    page_start: int | None
    page_end: int | None
    char_start: int
    char_end: int
    embedding_space: str | None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_chunk(cls, chunk: DocumentChunk) -> Self:
        return cls.model_validate(chunk)


class DocumentDetail(DocumentRead):
    chunks: list[ChunkRead] | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
    limit: int
    offset: int


# --- Search ---------------------------------------------------------------------------------------


class SearchFiltersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    sources: list[str] | None = Field(default=None, max_length=50)
    mime_types: list[str] | None = Field(default=None, max_length=10)
    metadata: dict[str, Any] | None = None
    """Containment match: `{"team": "billing"}` keeps documents whose metadata includes it."""
    created_after: datetime | None = None
    created_before: datetime | None = None

    _metadata_size = field_validator("metadata")(
        lambda value: check_metadata_size(value) if value is not None else None
    )

    def to_filters(self) -> DocumentFilters:
        return DocumentFilters(
            document_ids=tuple(self.document_ids) if self.document_ids else None,
            sources=tuple(self.sources) if self.sources else None,
            mime_types=tuple(self.mime_types) if self.mime_types else None,
            metadata=self.metadata or None,
            created_after=self.created_after,
            created_before=self.created_before,
        )


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: SearchFiltersIn | None = None
    recency_weight: float | None = Field(default=None, ge=0, le=1)
    max_context_tokens: int | None = Field(default=None, ge=100, le=200_000)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class CitationRead(BaseModel):
    index: int
    document_id: uuid.UUID
    chunk_ids: list[uuid.UUID]
    title: str
    source: str | None
    section: str | None
    page_start: int | None
    page_end: int | None
    label: str
    score: float
    snippet: str
    cited: bool | None = None
    """Set on completions: whether the answer referenced this citation."""

    @classmethod
    def from_citation(cls, citation: Citation, *, cited: bool | None = None) -> Self:
        return cls(
            index=citation.index,
            document_id=citation.document_id,
            chunk_ids=list(citation.chunk_ids),
            title=citation.title,
            source=citation.source,
            section=citation.section,
            page_start=citation.page_start,
            page_end=citation.page_end,
            label=citation.label,
            score=citation.score,
            snippet=citation.snippet,
            cited=cited,
        )


class SearchHitRead(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    source: str | None
    section: str | None
    chunk_index: int
    page_start: int | None
    page_end: int | None
    content: str
    score: float
    vector_similarity: float | None
    keyword_score: float | None
    vector_rank: int | None
    keyword_rank: int | None
    recency: float
    citation: int | None
    """Citation number when the chunk made it into the assembled context."""

    @classmethod
    def from_hit(cls, hit: SearchHit, citation: int | None) -> Self:
        return cls(
            chunk_id=hit.chunk.id,
            document_id=hit.document.id,
            title=hit.document.title,
            source=hit.document.source,
            section=hit.chunk.section,
            chunk_index=hit.chunk.chunk_index,
            page_start=hit.chunk.page_start,
            page_end=hit.chunk.page_end,
            content=hit.chunk.content,
            score=round(hit.score, 6),
            vector_similarity=_round(hit.fused.vector_similarity),
            keyword_score=_round(hit.fused.keyword_score),
            vector_rank=hit.fused.vector_rank,
            keyword_rank=hit.fused.keyword_rank,
            recency=round(hit.recency, 6),
            citation=citation,
        )


class ConfidenceRead(BaseModel):
    score: float
    level: ConfidenceLevel
    similarity: float | None
    coverage: float | None
    agreement: float | None


class ContextRead(BaseModel):
    text: str
    token_count: int
    truncated: bool
    citations: list[CitationRead]
    confidence: ConfidenceRead


class SearchMetrics(BaseModel):
    latency_ms: int
    embedding_ms: int
    vector_ms: int
    keyword_ms: int
    vector_candidates: int
    keyword_candidates: int
    query_embedding_cached: bool
    embedding_space: str | None
    query_terms: list[str]
    warnings: list[str]


class KnowledgeSearchResponse(BaseModel):
    query_id: uuid.UUID
    query: str
    mode: SearchMode
    results: list[SearchHitRead]
    context: ContextRead
    metrics: SearchMetrics

    @classmethod
    def build(cls, query_id: uuid.UUID, context: AssembledContext) -> Self:
        stats = context.search.stats
        cited = context.cited_hits
        return cls(
            query_id=query_id,
            query=context.query,
            mode=stats.mode,
            results=[SearchHitRead.from_hit(hit, cited.get(hit.chunk.id)) for hit in context.hits],
            context=context_read(context),
            metrics=SearchMetrics(
                latency_ms=stats.latency_ms,
                embedding_ms=stats.embedding_ms,
                vector_ms=stats.vector_ms,
                keyword_ms=stats.keyword_ms,
                vector_candidates=stats.vector_candidates,
                keyword_candidates=stats.keyword_candidates,
                query_embedding_cached=stats.query_embedding_cached,
                embedding_space=stats.embedding_space,
                query_terms=sorted(stats.query_lexemes),
                warnings=list(stats.warnings),
            ),
        )


def context_read(context: AssembledContext, cited: set[int] | None = None) -> ContextRead:
    confidence = context.confidence
    return ContextRead(
        text=context.text,
        token_count=context.token_count,
        truncated=context.truncated,
        citations=[
            CitationRead.from_citation(
                citation, cited=None if cited is None else citation.index in cited
            )
            for citation in context.citations
        ],
        confidence=ConfidenceRead(
            score=confidence.score,
            level=confidence.level,
            similarity=_round(confidence.similarity),
            coverage=_round(confidence.coverage),
            agreement=_round(confidence.agreement),
        ),
    )


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


# --- Completion grounding -------------------------------------------------------------------------


class KnowledgeOptions(BaseModel):
    """Grounds a completion in retrieved documents, with numbered citations."""

    model_config = ConfigDict(extra="forbid")

    top_k: int = Field(default=6, ge=1, le=50)
    mode: SearchMode = SearchMode.HYBRID
    filters: SearchFiltersIn | None = None
    max_context_tokens: int = Field(default=2000, ge=100, le=200_000)
    min_confidence: float = Field(default=0.0, ge=0, le=1)
    """Below this, the context is withheld from the model (the response still reports it)."""
    query: str | None = Field(default=None, max_length=2000)
    """Defaults to the last user message."""


class KnowledgeUsage(BaseModel):
    query_id: uuid.UUID
    applied: bool
    """False when nothing was retrieved or confidence was below `min_confidence`."""
    confidence: ConfidenceRead
    citations: list[CitationRead]
    cited: list[int]


# --- Query log ------------------------------------------------------------------------------------


class KnowledgeQueryRead(BaseModel):
    """One logged retrieval, as the citation inspector replays it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    query: str
    mode: SearchMode
    top_k: int
    filters: dict[str, Any]
    embedding_space: str | None
    result_count: int
    vector_candidates: int
    keyword_candidates: int
    citation_count: int
    context_tokens: int
    latency_ms: int
    embedding_ms: int
    vector_ms: int
    keyword_ms: int
    query_embedding_cached: bool
    top_score: float | None
    confidence: float
    coverage: float | None
    agreement: float | None
    completion_id: uuid.UUID | None
    cited: list[int] | None
    created_at: datetime


class KnowledgeQueryDetail(KnowledgeQueryRead):
    results: list[dict[str, Any]]


class KnowledgeQueryListResponse(BaseModel):
    items: list[KnowledgeQueryRead]
    total: int
    limit: int
    offset: int


# --- Metrics --------------------------------------------------------------------------------------


class CorpusMetrics(BaseModel):
    documents: int
    chunks: int
    tokens: int
    ingested: int
    avg_ingestion_ms: float | None
    p95_ingestion_ms: float | None
    avg_embedding_ms: float | None


class RetrievalMetrics(BaseModel):
    queries: int
    p50_ms: float | None
    p95_ms: float | None
    avg_embedding_ms: float | None
    avg_confidence: float | None
    avg_coverage: float | None
    avg_agreement: float | None
    zero_result_rate: float | None
    cache_hit_rate: float | None
    grounded_completions: int
    citations_offered: int
    citations_used: int
    citation_usage_rate: float | None


class KnowledgeMetricsResponse(BaseModel):
    window_hours: int
    corpus: CorpusMetrics
    retrieval: RetrievalMetrics

    @model_validator(mode="before")
    @classmethod
    def _derive_usage_rate(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("retrieval"), dict):
            retrieval = dict(data["retrieval"])
            offered = retrieval.get("citations_offered") or 0
            used = retrieval.get("citations_used") or 0
            retrieval.setdefault("citation_usage_rate", used / offered if offered else None)
            data = {**data, "retrieval": retrieval}
        return data
