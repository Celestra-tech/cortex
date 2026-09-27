import uuid
from collections.abc import Coroutine, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.database.ids import uuid7
from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.knowledge_query import KnowledgeQuery, SearchMode
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.knowledge_repository import (
    ChunkRepository,
    DocumentFilters,
    DocumentRepository,
    KnowledgeQueryRepository,
)
from cortex_api.services.knowledge.assembler import AssembledContext, ContextAssembler
from cortex_api.services.knowledge.chunker import DEFAULT_SEPARATORS, ChunkingConfig
from cortex_api.services.knowledge.embeddings import Embedder, QueryEmbeddingCache
from cortex_api.services.knowledge.extraction import DocumentFormat
from cortex_api.services.knowledge.hybrid_search import HybridSearcher, SearchConfig
from cortex_api.services.knowledge.ingestion import (
    DocumentInput,
    IngestionLimits,
    IngestionPipeline,
)
from cortex_api.services.observatory.events import EventPublisher, EventType


@dataclass(frozen=True, slots=True)
class Retrieval:
    query_id: uuid.UUID
    context: AssembledContext


class KnowledgeService:
    """Entry point for the knowledge layer. Owns the transaction for each call."""

    def __init__(
        self,
        session: AsyncSession,
        pipeline: IngestionPipeline,
        assembler: ContextAssembler,
        *,
        default_top_k: int = 8,
        events: EventPublisher | None = None,
    ) -> None:
        self.session = session
        self.pipeline = pipeline
        self.assembler = assembler
        self.default_top_k = default_top_k
        self.events = events or EventPublisher(None)
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)
        self.queries = KnowledgeQueryRepository(session)

    @classmethod
    def from_settings(
        cls, session: AsyncSession, redis: Redis | None, settings: Settings, embedder: Embedder
    ) -> Self:
        chunking = ChunkingConfig(
            chunk_size=settings.knowledge_chunk_size,
            chunk_overlap=settings.knowledge_chunk_overlap,
            separators=tuple(settings.knowledge_chunk_separators or DEFAULT_SEPARATORS),
        )
        pipeline = IngestionPipeline(
            session,
            embedder,
            chunking=chunking,
            limits=IngestionLimits(
                max_upload_bytes=settings.knowledge_max_upload_bytes,
                max_document_chars=settings.knowledge_max_document_chars,
                max_chunks=settings.knowledge_max_chunks_per_document,
            ),
        )
        cache = (
            QueryEmbeddingCache(
                redis,
                prefix=settings.knowledge_cache_key_prefix,
                ttl_seconds=settings.knowledge_query_cache_ttl_seconds,
            )
            if redis is not None
            else None
        )
        searcher = HybridSearcher(
            ChunkRepository(session),
            embedder,
            SearchConfig(
                rrf_k=settings.knowledge_rrf_k,
                vector_weight=settings.knowledge_vector_weight,
                keyword_weight=settings.knowledge_keyword_weight,
                recency_weight=settings.knowledge_recency_weight,
                recency_half_life_days=settings.knowledge_recency_half_life_days,
            ),
            cache,
        )
        assembler = ContextAssembler(
            searcher, max_context_tokens=settings.knowledge_max_context_tokens
        )
        return cls(
            session,
            pipeline,
            assembler,
            default_top_k=settings.knowledge_top_k,
            events=EventPublisher.from_settings(redis, settings),
        )

    @property
    def default_chunking(self) -> ChunkingConfig:
        return self.pipeline.chunking

    # --- Documents -------------------------------------------------------------------------------

    async def ingest_text(
        self,
        organization_id: uuid.UUID,
        text: str,
        document: DocumentInput,
        *,
        fmt: DocumentFormat = DocumentFormat.TEXT,
    ) -> Document:
        return await self._tracked(
            organization_id,
            {"title": document.title, "filename": None, "bytes": len(text.encode())},
            self.pipeline.ingest_text(organization_id, text, document, fmt=fmt),
        )

    async def ingest_file(
        self,
        organization_id: uuid.UUID,
        data: bytes,
        document: DocumentInput,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> Document:
        return await self._tracked(
            organization_id,
            {"title": document.title, "filename": filename, "bytes": len(data)},
            self.pipeline.ingest_file(
                organization_id, data, document, filename=filename, content_type=content_type
            ),
        )

    async def _tracked(
        self,
        organization_id: uuid.UUID,
        subject: dict[str, Any],
        work: Coroutine[Any, Any, Document],
    ) -> Document:
        """Runs one ingestion, bracketed by `document.ingesting` and its outcome event."""
        subject = {"ingest_id": uuid7(), **subject}
        await self.events.publish(organization_id, EventType.DOCUMENT_INGESTING, subject)
        try:
            row = await work
            await self.session.commit()
        except Exception as exc:
            # Validation errors are safe to show; anything else stays in the server log.
            error = str(exc) if isinstance(exc, ValueError) else "ingestion failed"
            await self.events.publish(
                organization_id,
                EventType.DOCUMENT_FAILED,
                {**subject, "error": error, "error_type": type(exc).__name__},
            )
            raise
        stats = row.ingestion_stats
        await self.events.publish(
            organization_id,
            EventType.DOCUMENT_INDEXED,
            {
                **subject,
                "document_id": row.id,
                "title": row.title,
                "mime_type": row.mime_type,
                "chunk_count": row.chunk_count,
                "embedded_chunks": stats.get("embedded_chunks", 0),
                "token_count": row.token_count,
                "embedding_space": row.embedding_space,
                "ingestion_ms": row.ingestion_ms,
            },
        )
        return row

    async def get_document(self, organization_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self.documents.get_for_organization(organization_id, document_id)
        if document is None:
            raise NotFoundError(Document, document_id)
        return document

    async def list_documents(
        self,
        organization_id: uuid.UUID,
        filters: DocumentFilters | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[Sequence[Document], int]:
        items = await self.documents.list_for_organization(
            organization_id, filters, limit=limit, offset=offset
        )
        total = await self.documents.count_for_organization(organization_id, filters)
        return items, total

    async def list_chunks(
        self, document: Document, *, limit: int | None = None, offset: int = 0
    ) -> Sequence[DocumentChunk]:
        return await self.chunks.list_for_document(document.id, limit=limit, offset=offset)

    async def delete_document(self, organization_id: uuid.UUID, document_id: uuid.UUID) -> None:
        document = await self.get_document(organization_id, document_id)
        await self.documents.delete(document, hard=True)
        await self.session.commit()
        await self.events.publish(
            organization_id,
            EventType.DOCUMENT_DELETED,
            {"document_id": document_id, "title": document.title},
        )

    # --- Retrieval -------------------------------------------------------------------------------

    async def retrieve(
        self,
        organization_id: uuid.UUID,
        query: str,
        *,
        top_k: int | None = None,
        mode: SearchMode = SearchMode.HYBRID,
        filters: DocumentFilters | None = None,
        recency_weight: float | None = None,
        max_context_tokens: int | None = None,
        commit: bool = True,
    ) -> Retrieval:
        """Searches, assembles context, and logs the retrieval.

        Pass `commit=False` when the caller commits later in the same unit of work.
        """
        top_k = top_k or self.default_top_k
        filters = filters or DocumentFilters()
        context = await self.assembler.assemble(
            organization_id,
            query,
            top_k=top_k,
            mode=mode,
            filters=filters,
            recency_weight=recency_weight,
            max_context_tokens=max_context_tokens,
        )
        entry = await self.queries.record(_log_entry(organization_id, top_k, filters, context))
        if commit:
            await self.session.commit()
        return Retrieval(entry.id, context)

    async def record_citations(
        self, query_id: uuid.UUID, *, completion_id: uuid.UUID, cited: Sequence[int]
    ) -> None:
        """Links a retrieval to the completion it grounded. The caller commits."""
        await self.queries.mark_cited(query_id, completion_id=completion_id, cited=cited)

    async def get_query(self, organization_id: uuid.UUID, query_id: uuid.UUID) -> KnowledgeQuery:
        entry = await self.queries.get_for_organization(organization_id, query_id)
        if entry is None:
            raise NotFoundError(KnowledgeQuery, query_id)
        return entry

    async def list_queries(
        self, organization_id: uuid.UUID, *, limit: int, offset: int = 0
    ) -> tuple[Sequence[KnowledgeQuery], int]:
        return (
            await self.queries.list_for_organization(organization_id, limit=limit, offset=offset),
            await self.queries.count_for_organization(organization_id),
        )

    async def metrics(self, organization_id: uuid.UUID, window: timedelta) -> dict[str, Any]:
        since = datetime.now(UTC) - window
        return {
            "corpus": await self.documents.metrics(organization_id, since),
            "retrieval": await self.queries.metrics(organization_id, since),
        }


def _log_entry(
    organization_id: uuid.UUID, top_k: int, filters: DocumentFilters, context: AssembledContext
) -> KnowledgeQuery:
    stats = context.search.stats
    cited = context.cited_hits
    return KnowledgeQuery(
        organization_id=organization_id,
        query=context.query,
        mode=stats.mode,
        top_k=top_k,
        filters=filters.as_json(),
        embedding_space=stats.embedding_space,
        result_count=len(context.hits),
        vector_candidates=stats.vector_candidates,
        keyword_candidates=stats.keyword_candidates,
        citation_count=len(context.citations),
        context_tokens=context.token_count,
        latency_ms=stats.latency_ms,
        embedding_ms=stats.embedding_ms,
        vector_ms=stats.vector_ms,
        keyword_ms=stats.keyword_ms,
        query_embedding_cached=stats.query_embedding_cached,
        top_score=context.hits[0].score if context.hits else None,
        confidence=context.confidence.score,
        coverage=context.confidence.coverage,
        agreement=context.confidence.agreement,
        results=[
            {
                "chunk_id": str(hit.chunk.id),
                "document_id": str(hit.document.id),
                "score": round(hit.score, 6),
                "citation": cited.get(hit.chunk.id),
            }
            for hit in context.hits
        ],
    )
