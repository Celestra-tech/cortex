import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Float,
    Select,
    String,
    and_,
    case,
    cast,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.orm import InstrumentedAttribute

from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.embedding import Embedding
from cortex_api.models.knowledge_query import KnowledgeQuery
from cortex_api.models.memory import SEARCH_CONFIG
from cortex_api.repositories.base import BaseRepository

MAX_QUERY_CHARS = 2_000


@dataclass(frozen=True, slots=True)
class DocumentFilters:
    """Metadata filters shared by document listing and chunk retrieval."""

    document_ids: tuple[uuid.UUID, ...] | None = None
    sources: tuple[str, ...] | None = None
    mime_types: tuple[str, ...] | None = None
    metadata: dict[str, Any] | None = None
    """JSONB containment: every given key must match."""
    created_after: datetime | None = None
    created_before: datetime | None = None

    @property
    def needs_document_join(self) -> bool:
        return bool(
            self.sources
            or self.mime_types
            or self.metadata
            or self.created_after
            or self.created_before
        )

    def document_clauses(self) -> list[ColumnElement[bool]]:
        clauses: list[ColumnElement[bool]] = []
        if self.document_ids:
            clauses.append(Document.id.in_(self.document_ids))
        if self.sources:
            clauses.append(Document.source.in_(self.sources))
        if self.mime_types:
            clauses.append(Document.mime_type.in_(self.mime_types))
        if self.metadata:
            clauses.append(Document.metadata_.contains(self.metadata))
        if self.created_after:
            clauses.append(Document.created_at >= self.created_after)
        if self.created_before:
            clauses.append(Document.created_at < self.created_before)
        return clauses

    def as_json(self) -> dict[str, Any]:
        """Compact form for the retrieval log."""
        values: dict[str, Any] = {
            "document_ids": [str(i) for i in self.document_ids] if self.document_ids else None,
            "sources": list(self.sources) if self.sources else None,
            "mime_types": list(self.mime_types) if self.mime_types else None,
            "metadata": self.metadata or None,
            "created_after": self.created_after.isoformat() if self.created_after else None,
            "created_before": self.created_before.isoformat() if self.created_before else None,
        }
        return {key: value for key, value in values.items() if value is not None}


class DocumentRepository(BaseRepository[Document]):
    model = Document
    default_limit = 50
    max_limit = 200

    async def get_for_organization(
        self, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document | None:
        statement = select(Document).where(
            Document.id == document_id, Document.organization_id == organization_id
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_content_hash(
        self, organization_id: uuid.UUID, content_hash: str
    ) -> Document | None:
        statement = select(Document).where(
            Document.organization_id == organization_id, Document.content_hash == content_hash
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        filters: DocumentFilters | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[Document]:
        statement = (
            select(Document)
            .where(Document.organization_id == organization_id)
            .where(*(filters or DocumentFilters()).document_clauses())
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(min(limit or self.default_limit, self.max_limit))
            .offset(offset)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def count_for_organization(
        self, organization_id: uuid.UUID, filters: DocumentFilters | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Document)
            .where(Document.organization_id == organization_id)
            .where(*(filters or DocumentFilters()).document_clauses())
        )
        return (await self.session.execute(statement)).scalar_one()

    async def metrics(self, organization_id: uuid.UUID, since: datetime) -> dict[str, Any]:
        """Corpus totals, plus ingestion timings for documents created since `since`."""
        recent = Document.created_at >= since
        statement = select(
            func.count().label("documents"),
            func.coalesce(func.sum(Document.chunk_count), 0).label("chunks"),
            func.coalesce(func.sum(Document.token_count), 0).label("tokens"),
            func.count().filter(recent).label("ingested"),
            func.avg(Document.ingestion_ms).filter(recent).label("avg_ingestion_ms"),
            func.percentile_cont(0.95)
            .within_group(Document.ingestion_ms)
            .filter(recent)
            .label("p95_ingestion_ms"),
            func.avg(Document.embedding_ms).filter(recent).label("avg_embedding_ms"),
        ).where(Document.organization_id == organization_id)
        row = (await self.session.execute(statement)).one()
        return dict(row._mapping)


@dataclass(frozen=True, slots=True)
class ChunkHit:
    chunk: DocumentChunk
    document: Document
    lexemes: frozenset[str]


class ChunkRepository(BaseRepository[DocumentChunk]):
    model = DocumentChunk
    default_limit = 500
    max_limit = 10_000

    async def add_many(
        self, chunks: Sequence[DocumentChunk], embeddings: Sequence[Embedding] = ()
    ) -> None:
        self.session.add_all(chunks)
        await self.session.flush()
        if embeddings:
            self.session.add_all(embeddings)
            await self.session.flush()

    async def list_for_document(
        self, document_id: uuid.UUID, *, limit: int | None = None, offset: int = 0
    ) -> Sequence[DocumentChunk]:
        statement = (
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
            .limit(min(limit or self.default_limit, self.max_limit))
            .offset(offset)
        )
        return (await self.session.execute(statement)).scalars().all()

    def _scoped(
        self,
        statement: Select[uuid.UUID, float],
        organization_id: uuid.UUID,
        filters: DocumentFilters,
    ) -> Select[uuid.UUID, float]:
        statement = statement.where(DocumentChunk.organization_id == organization_id)
        if filters.document_ids:
            statement = statement.where(DocumentChunk.document_id.in_(filters.document_ids))
        if filters.needs_document_join:
            document_filters = DocumentFilters(
                sources=filters.sources,
                mime_types=filters.mime_types,
                metadata=filters.metadata,
                created_after=filters.created_after,
                created_before=filters.created_before,
            )
            statement = statement.join(
                Document,
                and_(
                    Document.id == DocumentChunk.document_id,
                    *document_filters.document_clauses(),
                ),
            )
        return statement

    async def vector_candidates(
        self,
        organization_id: uuid.UUID,
        space: str,
        vector: Sequence[float],
        filters: DocumentFilters,
        limit: int,
    ) -> list[tuple[uuid.UUID, float]]:
        """(chunk id, cosine similarity), most similar first."""
        # Iterative scans keep filtered HNSW queries from returning fewer than `limit` rows.
        await self.session.execute(
            select(
                func.set_config("hnsw.ef_search", str(min(max(limit, 40), 1000)), True),
                func.set_config("hnsw.iterative_scan", "relaxed_order", True),
            )
        )
        distance = DocumentChunk.embedding.cosine_distance(list(vector))
        statement = self._scoped(
            select(DocumentChunk.id, cast(distance, Float).label("distance")).where(
                DocumentChunk.embedding_space == space
            ),
            organization_id,
            filters,
        )
        rows = (await self.session.execute(statement.order_by(distance).limit(limit))).all()
        # relaxed_order may return rows slightly out of order.
        ranked = sorted(((row[0], 1.0 - float(row[1])) for row in rows), key=lambda r: -r[1])
        return ranked

    async def keyword_candidates(
        self,
        organization_id: uuid.UUID,
        query: str,
        filters: DocumentFilters,
        limit: int,
    ) -> list[tuple[uuid.UUID, float]]:
        """(chunk id, ts_rank_cd normalized into [0, 1)), best first. Terms are OR-ed."""
        tsquery = _disjunctive_tsquery(query)
        rank = cast(func.ts_rank_cd(DocumentChunk.search_vector, tsquery, 32), Float)
        statement = self._scoped(
            select(DocumentChunk.id, rank.label("rank")).where(
                DocumentChunk.search_vector.bool_op("@@")(tsquery)
            ),
            organization_id,
            filters,
        )
        statement = statement.order_by(rank.desc(), DocumentChunk.id).limit(limit)
        return [(row[0], float(row[1])) for row in (await self.session.execute(statement)).all()]

    async def load_hits(self, chunk_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, ChunkHit]:
        if not chunk_ids:
            return {}
        statement = (
            select(DocumentChunk, Document, func.tsvector_to_array(DocumentChunk.search_vector))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.id.in_(chunk_ids))
        )
        rows = (await self.session.execute(statement)).all()
        return {
            chunk.id: ChunkHit(chunk, document, frozenset(lexemes or ()))
            for chunk, document, lexemes in rows
        }

    async def query_lexemes(self, query: str) -> frozenset[str]:
        """Stemmed, stop-word-free terms, as the keyword index sees them."""
        statement = select(
            func.tsvector_to_array(func.to_tsvector(SEARCH_CONFIG, query[:MAX_QUERY_CHARS]))
        )
        return frozenset((await self.session.execute(statement)).scalar_one() or ())


def _disjunctive_tsquery(query: str) -> ColumnElement[Any]:
    # plainto_tsquery stems and escapes arbitrary input; swapping & for | gives OR semantics.
    conjunctive = cast(func.plainto_tsquery(SEARCH_CONFIG, query[:MAX_QUERY_CHARS]), String)
    return cast(func.replace(conjunctive, "&", "|"), TSQUERY)


def _ratio(condition: ColumnElement[bool] | InstrumentedAttribute[bool]) -> ColumnElement[float]:
    """1.0 when the condition holds, else 0.0, for averaging into a rate.

    Postgres cannot cast boolean to double precision directly.
    """
    return case((condition, 1.0), else_=0.0)


class KnowledgeQueryRepository(BaseRepository[KnowledgeQuery]):
    model = KnowledgeQuery

    async def record(self, entry: KnowledgeQuery) -> KnowledgeQuery:
        return await self.add(entry)

    async def get_for_organization(
        self, organization_id: uuid.UUID, query_id: uuid.UUID
    ) -> KnowledgeQuery | None:
        statement = select(KnowledgeQuery).where(
            KnowledgeQuery.id == query_id, KnowledgeQuery.organization_id == organization_id
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, limit: int, offset: int = 0
    ) -> Sequence[KnowledgeQuery]:
        """Newest first."""
        statement = (
            select(KnowledgeQuery)
            .where(KnowledgeQuery.organization_id == organization_id)
            .order_by(KnowledgeQuery.created_at.desc(), KnowledgeQuery.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        statement = select(func.count()).where(KnowledgeQuery.organization_id == organization_id)
        return (await self.session.execute(statement)).scalar_one()

    async def mark_cited(
        self, query_id: uuid.UUID, *, completion_id: uuid.UUID, cited: Sequence[int]
    ) -> None:
        await self.session.execute(
            update(KnowledgeQuery)
            .where(KnowledgeQuery.id == query_id)
            .values(completion_id=completion_id, cited=list(cited))
        )

    async def metrics(self, organization_id: uuid.UUID, since: datetime) -> dict[str, Any]:
        latency = KnowledgeQuery.latency_ms
        used = KnowledgeQuery.completion_id.is_not(None)
        cited_count = func.coalesce(func.jsonb_array_length(KnowledgeQuery.cited), 0)
        statement = select(
            func.count().label("queries"),
            func.percentile_cont(0.5).within_group(latency).label("p50_ms"),
            func.percentile_cont(0.95).within_group(latency).label("p95_ms"),
            func.avg(KnowledgeQuery.embedding_ms).label("avg_embedding_ms"),
            func.avg(KnowledgeQuery.confidence).label("avg_confidence"),
            func.avg(KnowledgeQuery.coverage).label("avg_coverage"),
            func.avg(KnowledgeQuery.agreement).label("avg_agreement"),
            func.avg(_ratio(KnowledgeQuery.result_count == 0)).label("zero_result_rate"),
            func.avg(_ratio(KnowledgeQuery.query_embedding_cached)).label("cache_hit_rate"),
            func.count().filter(used).label("grounded_completions"),
            func.coalesce(func.sum(KnowledgeQuery.citation_count).filter(used), 0).label(
                "citations_offered"
            ),
            func.coalesce(func.sum(cited_count).filter(used), 0).label("citations_used"),
        ).where(
            KnowledgeQuery.organization_id == organization_id, KnowledgeQuery.created_at >= since
        )
        row = (await self.session.execute(statement)).one()
        return dict(row._mapping)
