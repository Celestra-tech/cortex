"""Hybrid retrieval: vector similarity + keyword rank, fused, filtered, recency-boosted.

1. **Candidates.** Up to `pool` chunks from each retriever, both scoped to the
   organization and the metadata filters: nearest neighbours by cosine distance
   (pgvector HNSW) in the active embedding space, and full-text matches ranked
   by `ts_rank_cd` (section headings weigh more than body text).
2. **Fusion.** Reciprocal rank fusion:
   `sum(weight / (k + rank))` over the retrievers that found the chunk,
   normalized so a chunk ranked first by every active retriever scores 1.
   RRF uses ranks, not raw scores, so cosine similarities and text-search
   ranks never need to be put on a common scale.
3. **Recency.** `score *= 1 - w + w * 0.5 ** (age / half_life)`, so the
   boost can cost an old document at most `w` of its score.

If the embedding provider fails, hybrid search degrades to keyword-only and
says so in `SearchStats.warnings`; pure vector search raises instead.
"""

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.knowledge_query import SearchMode
from cortex_api.repositories.knowledge_repository import (
    MAX_QUERY_CHARS,
    ChunkRepository,
    DocumentFilters,
)
from cortex_api.services.knowledge.embeddings import (
    Embedder,
    EmbeddingTask,
    QueryEmbeddingCache,
    Vector,
    to_index_vector,
)
from cortex_api.services.router.base import ProviderError

SECONDS_PER_DAY = 86_400.0


@dataclass(frozen=True, slots=True)
class SearchConfig:
    rrf_k: int = 60
    vector_weight: float = 1.0
    keyword_weight: float = 1.0
    recency_weight: float = 0.1
    recency_half_life_days: float = 180.0
    candidate_multiplier: int = 4
    min_candidates: int = 40
    max_candidates: int = 400
    rerank_multiplier: int = 3
    """Fused candidates loaded per requested result, before recency reorders them."""

    def pool(self, top_k: int) -> int:
        return max(self.min_candidates, min(self.max_candidates, top_k * self.candidate_multiplier))


@dataclass(frozen=True, slots=True)
class FusedScore:
    score: float
    vector_rank: int | None = None
    vector_similarity: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None

    @property
    def both(self) -> bool:
        return self.vector_rank is not None and self.keyword_rank is not None


def reciprocal_rank_fusion(
    vector: Sequence[tuple[uuid.UUID, float]],
    keyword: Sequence[tuple[uuid.UUID, float]],
    *,
    k: int = 60,
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> dict[uuid.UUID, FusedScore]:
    """Fuses two best-first lists into scores in (0, 1]."""
    vector_weight = vector_weight if vector else 0.0
    keyword_weight = keyword_weight if keyword else 0.0
    ceiling = (vector_weight + keyword_weight) / (k + 1)
    if ceiling == 0:
        return {}

    vector_hits = {chunk_id: (rank, score) for rank, (chunk_id, score) in enumerate(vector, 1)}
    keyword_hits = {chunk_id: (rank, score) for rank, (chunk_id, score) in enumerate(keyword, 1)}
    fused: dict[uuid.UUID, FusedScore] = {}
    for chunk_id in vector_hits.keys() | keyword_hits.keys():
        v = vector_hits.get(chunk_id)
        kw = keyword_hits.get(chunk_id)
        raw = (vector_weight / (k + v[0]) if v else 0.0) + (
            keyword_weight / (k + kw[0]) if kw else 0.0
        )
        fused[chunk_id] = FusedScore(
            score=raw / ceiling,
            vector_rank=v[0] if v else None,
            vector_similarity=v[1] if v else None,
            keyword_rank=kw[0] if kw else None,
            keyword_score=kw[1] if kw else None,
        )
    return fused


def recency_factor(created_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (now - created_at).total_seconds() / SECONDS_PER_DAY)
    return float(0.5 ** (age_days / half_life_days))


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk: DocumentChunk
    document: Document
    score: float
    fused: FusedScore
    recency: float
    lexemes: frozenset[str]


@dataclass(frozen=True, slots=True)
class SearchStats:
    mode: SearchMode
    embedding_space: str | None
    query_lexemes: frozenset[str]
    vector_candidates: int = 0
    keyword_candidates: int = 0
    embedding_ms: int = 0
    vector_ms: int = 0
    keyword_ms: int = 0
    latency_ms: int = 0
    query_embedding_cached: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[SearchHit]
    stats: SearchStats


@dataclass(frozen=True, slots=True)
class _QueryVector:
    vector: Vector | None
    latency_ms: int
    cached: bool


class VectorSearchUnavailableError(Exception):
    status_code = 502

    def __init__(self, error: ProviderError) -> None:
        super().__init__(f"vector search unavailable: {error.describe()}")
        self.error = error


@dataclass
class HybridSearcher:
    chunks: ChunkRepository
    embedder: Embedder
    config: SearchConfig = field(default_factory=SearchConfig)
    cache: QueryEmbeddingCache | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))

    async def search(
        self,
        organization_id: uuid.UUID,
        query: str,
        *,
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: DocumentFilters | None = None,
        recency_weight: float | None = None,
        min_score: float = 0.0,
    ) -> SearchResult:
        started = time.perf_counter()
        query = " ".join(query.split())[:MAX_QUERY_CHARS]
        filters = filters or DocumentFilters()
        pool = self.config.pool(top_k)
        warnings: list[str] = []

        vector_ranked: list[tuple[uuid.UUID, float]] = []
        query_vector = _QueryVector(None, 0, False)
        vector_ms = 0
        if mode is not SearchMode.KEYWORD:
            try:
                query_vector = await self._query_vector(query)
            except ProviderError as error:
                if mode is SearchMode.VECTOR:
                    raise VectorSearchUnavailableError(error) from error
                warnings.append(f"vector search skipped: {error.describe()}")
            if query_vector.vector is not None:
                stage = time.perf_counter()
                vector_ranked = await self.chunks.vector_candidates(
                    organization_id,
                    self.embedder.space,
                    to_index_vector(query_vector.vector),
                    filters,
                    pool,
                )
                vector_ms = _elapsed_ms(stage)

        keyword_ranked: list[tuple[uuid.UUID, float]] = []
        keyword_ms = 0
        if mode is not SearchMode.VECTOR:
            stage = time.perf_counter()
            keyword_ranked = await self.chunks.keyword_candidates(
                organization_id, query, filters, pool
            )
            keyword_ms = _elapsed_ms(stage)

        fused = reciprocal_rank_fusion(
            vector_ranked,
            keyword_ranked,
            k=self.config.rrf_k,
            vector_weight=self.config.vector_weight,
            keyword_weight=self.config.keyword_weight,
        )
        shortlist = sorted(fused, key=lambda chunk_id: -fused[chunk_id].score)
        shortlist = shortlist[: top_k * self.config.rerank_multiplier]
        loaded = await self.chunks.load_hits(shortlist)
        lexemes = await self.chunks.query_lexemes(query)

        weight = self.config.recency_weight if recency_weight is None else recency_weight
        now = self.clock()
        hits: list[SearchHit] = []
        for chunk_id in shortlist:
            hit = loaded.get(chunk_id)
            if hit is None:
                continue
            recency = recency_factor(
                hit.document.created_at, now, self.config.recency_half_life_days
            )
            score = fused[chunk_id].score * (1.0 - weight + weight * recency)
            if score >= min_score:
                hits.append(
                    SearchHit(hit.chunk, hit.document, score, fused[chunk_id], recency, hit.lexemes)
                )
        hits.sort(key=lambda h: (-h.score, -(h.fused.vector_similarity or 0.0), str(h.chunk.id)))

        stats = SearchStats(
            mode=mode,
            embedding_space=self.embedder.space if mode is not SearchMode.KEYWORD else None,
            query_lexemes=lexemes,
            vector_candidates=len(vector_ranked),
            keyword_candidates=len(keyword_ranked),
            embedding_ms=query_vector.latency_ms,
            vector_ms=vector_ms,
            keyword_ms=keyword_ms,
            latency_ms=_elapsed_ms(started),
            query_embedding_cached=query_vector.cached,
            warnings=tuple(warnings),
        )
        return SearchResult(hits[:top_k], stats)

    async def _query_vector(self, query: str) -> _QueryVector:
        stage = time.perf_counter()
        space = self.embedder.space
        if self.cache is not None:
            cached = await self.cache.get(space, query)
            if cached is not None and len(cached) == self.embedder.provider.dimensions:
                return _QueryVector(cached, _elapsed_ms(stage), True)
        batch = await self.embedder.embed([query], EmbeddingTask.QUERY)
        vector = batch.vectors[0]
        if vector is not None and self.cache is not None:
            await self.cache.set(space, query, vector)
        return _QueryVector(vector, _elapsed_ms(stage), False)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
