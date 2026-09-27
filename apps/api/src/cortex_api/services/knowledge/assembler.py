"""Retrieval results -> a citation-annotated context window.

* **Merging.** Hits from the same document with adjacent or overlapping
  chunk indices become one passage. Offsets are exact, so the overlap between
  neighbouring chunks is included only once.
* **Packing.** Passages are added best-first until the token budget is spent.
  A passage that does not fit is skipped, so a smaller, lower-ranked one can
  still use the remaining space.
* **Confidence** blends three signals, each in [0, 1]:
  - similarity: the best vector similarity among packed passages, rescaled
    by the embedding provider's typical similarity range;
  - coverage: the share of query terms that appear somewhere in the context;
  - agreement: the share of packed chunks that both retrievers found.
  Signals that do not apply (no vector search, for example) are dropped and
  the remaining weights renormalized. This is a heuristic for deciding
  whether to trust the context, not a calibrated probability.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal

from cortex_api.models.knowledge_query import SearchMode
from cortex_api.repositories.knowledge_repository import DocumentFilters
from cortex_api.services.knowledge.citations import Citation, render_sources, snippet
from cortex_api.services.knowledge.hybrid_search import HybridSearcher, SearchHit, SearchResult
from cortex_api.services.memory.encoder import HeuristicTokenEstimator, TokenEstimator

ConfidenceLevel = Literal["high", "medium", "low", "none"]

CONFIDENCE_WEIGHTS = {"similarity": 0.5, "coverage": 0.35, "agreement": 0.15}


@dataclass(frozen=True, slots=True)
class Passage:
    document_id: uuid.UUID
    hits: tuple[SearchHit, ...]
    text: str

    @property
    def score(self) -> float:
        return max(hit.score for hit in self.hits)

    @property
    def lead(self) -> SearchHit:
        return max(self.hits, key=lambda hit: hit.score)


@dataclass(frozen=True, slots=True)
class Confidence:
    score: float
    level: ConfidenceLevel
    similarity: float | None
    coverage: float | None
    agreement: float | None


@dataclass(frozen=True, slots=True)
class AssembledContext:
    query: str
    text: str
    """Rendered sources, ready to place in a system message."""
    citations: list[Citation]
    passages: list[Passage]
    token_count: int
    truncated: bool
    """True when some retrieved passages did not fit the budget."""
    confidence: Confidence
    search: SearchResult

    @property
    def hits(self) -> list[SearchHit]:
        return self.search.hits

    @property
    def cited_hits(self) -> dict[uuid.UUID, int]:
        """Chunk id -> citation number, for chunks that made it into the context."""
        return {
            chunk_id: citation.index
            for citation in self.citations
            for chunk_id in citation.chunk_ids
        }


def merge_adjacent(hits: list[SearchHit]) -> list[Passage]:
    """Groups hits into passages of consecutive chunks, ordered by best score."""
    by_document: dict[uuid.UUID, list[SearchHit]] = {}
    for hit in hits:
        by_document.setdefault(hit.document.id, []).append(hit)

    passages: list[Passage] = []
    for document_id, group in by_document.items():
        group.sort(key=lambda h: h.chunk.chunk_index)
        run: list[SearchHit] = [group[0]]
        for hit in group[1:]:
            if hit.chunk.chunk_index == run[-1].chunk.chunk_index + 1:
                run.append(hit)
            else:
                passages.append(_passage(document_id, run))
                run = [hit]
        passages.append(_passage(document_id, run))
    passages.sort(key=lambda p: -p.score)
    return passages


def _passage(document_id: uuid.UUID, run: list[SearchHit]) -> Passage:
    text = run[0].chunk.content
    end = run[0].chunk.char_end
    for hit in run[1:]:
        chunk = hit.chunk
        if chunk.char_start < end:
            text += chunk.content[end - chunk.char_start :]
        else:
            text += "\n\n" + chunk.content
        end = max(end, chunk.char_end)
    return Passage(document_id, tuple(run), text)


def confidence(
    passages: list[Passage],
    query_lexemes: frozenset[str],
    similarity_range: tuple[float, float],
    mode: SearchMode,
) -> Confidence:
    if not passages:
        return Confidence(0.0, "none", None, None, None)
    hits = [hit for passage in passages for hit in passage.hits]

    similarities = [
        h.fused.vector_similarity for h in hits if h.fused.vector_similarity is not None
    ]
    low, high = similarity_range
    similarity = (
        _clamp((max(similarities) - low) / (high - low)) if similarities and high > low else None
    )
    present = frozenset().union(*(hit.lexemes for hit in hits))
    coverage = len(query_lexemes & present) / len(query_lexemes) if query_lexemes else None
    agreement = (
        sum(hit.fused.both for hit in hits) / len(hits) if mode is SearchMode.HYBRID else None
    )

    signals = {"similarity": similarity, "coverage": coverage, "agreement": agreement}
    active = {name: value for name, value in signals.items() if value is not None}
    if not active:
        return Confidence(0.0, "none", similarity, coverage, agreement)
    total_weight = sum(CONFIDENCE_WEIGHTS[name] for name in active)
    score = sum(CONFIDENCE_WEIGHTS[name] * value for name, value in active.items()) / total_weight
    return Confidence(round(score, 4), _level(score), similarity, coverage, agreement)


def _level(score: float) -> ConfidenceLevel:
    if score >= 0.7:
        return "high"
    if score >= 0.4:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass
class ContextAssembler:
    searcher: HybridSearcher
    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)
    max_context_tokens: int = 3000
    snippet_chars: int = 240

    async def assemble(
        self,
        organization_id: uuid.UUID,
        query: str,
        *,
        top_k: int,
        mode: SearchMode = SearchMode.HYBRID,
        filters: DocumentFilters | None = None,
        recency_weight: float | None = None,
        max_context_tokens: int | None = None,
    ) -> AssembledContext:
        result = await self.searcher.search(
            organization_id,
            query,
            top_k=top_k,
            mode=mode,
            filters=filters,
            recency_weight=recency_weight,
        )
        budget = max_context_tokens or self.max_context_tokens
        terms = sorted(result.stats.query_lexemes)

        packed: list[tuple[Citation, str]] = []
        passages: list[Passage] = []
        used = 0
        truncated = False
        for passage in merge_adjacent(result.hits):
            citation = self._citation(len(packed) + 1, passage, terms)
            block = render_sources([(citation, passage.text)])
            cost = self.estimator.count(block)
            if used + cost > budget:
                truncated = True
                continue
            packed.append((citation, passage.text))
            passages.append(passage)
            used += cost

        return AssembledContext(
            query=query,
            text=render_sources(packed),
            citations=[citation for citation, _ in packed],
            passages=passages,
            token_count=used,
            truncated=truncated,
            confidence=confidence(
                passages,
                result.stats.query_lexemes,
                self.searcher.embedder.provider.similarity_range,
                result.stats.mode,
            ),
            search=result,
        )

    def _citation(self, index: int, passage: Passage, terms: list[str]) -> Citation:
        lead = passage.lead
        pages = [
            (hit.chunk.page_start, hit.chunk.page_end)
            for hit in passage.hits
            if hit.chunk.page_start is not None
        ]
        return Citation(
            index=index,
            document_id=passage.document_id,
            chunk_ids=tuple(hit.chunk.id for hit in passage.hits),
            title=lead.document.title,
            source=lead.document.source,
            section=lead.chunk.section,
            page_start=min(start for start, _ in pages) if pages else None,
            page_end=max(end or start for start, end in pages) if pages else None,
            score=round(passage.score, 6),
            snippet=snippet(lead.chunk.content, terms, max_chars=self.snippet_chars),
        )
