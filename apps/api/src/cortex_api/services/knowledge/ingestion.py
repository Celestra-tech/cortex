"""Upload -> extract -> normalize -> chunk -> embed -> store.

The pipeline flushes but never commits; `KnowledgeService` owns the
transaction, so a failure at any stage leaves nothing behind.
"""

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.embedding import Embedding
from cortex_api.repositories.knowledge_repository import ChunkRepository, DocumentRepository
from cortex_api.services.knowledge.chunker import Chunk, ChunkingConfig, SemanticChunker
from cortex_api.services.knowledge.embeddings import Embedder, EmbeddingTask, to_index_vector
from cortex_api.services.knowledge.extraction import (
    DocumentFormat,
    ExtractedText,
    ExtractionError,
    detect_format,
    extract,
    extract_text,
)
from cortex_api.services.memory.encoder import HeuristicTokenEstimator, TokenEstimator


class IngestionError(ValueError):
    status_code = 422


class DocumentTooLargeError(IngestionError):
    status_code = 413


class DuplicateDocumentError(IngestionError):
    status_code = 409

    def __init__(self, document_id: uuid.UUID) -> None:
        super().__init__(f"identical content already ingested as document {document_id}")
        self.document_id = document_id


@dataclass(frozen=True, slots=True)
class IngestionLimits:
    max_upload_bytes: int = 25 * 1024 * 1024
    max_document_chars: int = 5_000_000
    max_chunks: int = 10_000


@dataclass(frozen=True, slots=True)
class DocumentInput:
    title: str | None = None
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    chunking: ChunkingConfig | None = None


def embedding_text(title: str, chunk: Chunk) -> str:
    """What gets embedded: the chunk prefixed with its document title and heading path.

    The header disambiguates chunks that only make sense in context
    ("Refunds take 5 days" means little without "Billing > Refunds").
    """
    header = f"{title} > {chunk.section}" if chunk.section else title
    return f"{header}\n\n{chunk.content}"


class IngestionPipeline:
    def __init__(
        self,
        session: AsyncSession,
        embedder: Embedder,
        *,
        chunking: ChunkingConfig | None = None,
        limits: IngestionLimits | None = None,
        estimator: TokenEstimator | None = None,
    ) -> None:
        self.session = session
        self.documents = DocumentRepository(session)
        self.chunks = ChunkRepository(session)
        self.embedder = embedder
        self.chunking = chunking or ChunkingConfig()
        self.limits = limits or IngestionLimits()
        self.estimator = estimator or HeuristicTokenEstimator()

    async def ingest_file(
        self,
        organization_id: uuid.UUID,
        data: bytes,
        document: DocumentInput,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> Document:
        started = time.perf_counter()
        if len(data) > self.limits.max_upload_bytes:
            raise DocumentTooLargeError(
                f"upload is {len(data)} bytes; the limit is {self.limits.max_upload_bytes}"
            )
        if not data:
            raise ExtractionError("upload is empty")
        fmt = detect_format(data, filename=filename, declared_type=content_type)
        extracted = await asyncio.to_thread(extract, data, fmt)
        fallback_title = PurePath(filename).stem if filename else None
        return await self._store(
            organization_id,
            extracted,
            document,
            byte_size=len(data),
            fallback_title=fallback_title,
            started=started,
            extract_ms=_elapsed_ms(started),
        )

    async def ingest_text(
        self,
        organization_id: uuid.UUID,
        text: str,
        document: DocumentInput,
        *,
        fmt: DocumentFormat = DocumentFormat.TEXT,
    ) -> Document:
        started = time.perf_counter()
        byte_size = len(text.encode())
        if byte_size > self.limits.max_upload_bytes:
            raise DocumentTooLargeError(
                f"content is {byte_size} bytes; the limit is {self.limits.max_upload_bytes}"
            )
        extracted = extract_text(text, fmt)
        return await self._store(
            organization_id,
            extracted,
            document,
            byte_size=byte_size,
            fallback_title=None,
            started=started,
            extract_ms=_elapsed_ms(started),
        )

    async def _store(
        self,
        organization_id: uuid.UUID,
        extracted: ExtractedText,
        document: DocumentInput,
        *,
        byte_size: int,
        fallback_title: str | None,
        started: float,
        extract_ms: int,
    ) -> Document:
        text = extracted.text
        if not text:
            raise ExtractionError("document has no text")
        if len(text) > self.limits.max_document_chars:
            raise DocumentTooLargeError(
                f"document has {len(text)} characters; the limit is "
                f"{self.limits.max_document_chars}"
            )

        content_hash = hashlib.sha256(text.encode()).hexdigest()
        existing = await self.documents.get_by_content_hash(organization_id, content_hash)
        if existing is not None:
            raise DuplicateDocumentError(existing.id)

        title = (document.title or extracted.title or fallback_title or "Untitled").strip()[:512]
        chunking = document.chunking or self.chunking

        stage = time.perf_counter()
        chunker = SemanticChunker(chunking, self.estimator)
        chunks = chunker.chunk(text, page_offsets=extracted.page_offsets)
        chunk_ms = _elapsed_ms(stage)
        if len(chunks) > self.limits.max_chunks:
            raise DocumentTooLargeError(
                f"document produces {len(chunks)} chunks; the limit is {self.limits.max_chunks}"
            )

        batch = await self.embedder.embed(
            [embedding_text(title, chunk) for chunk in chunks], EmbeddingTask.DOCUMENT
        )
        embedded = sum(vector is not None for vector in batch.vectors)
        provider = self.embedder.provider

        stage = time.perf_counter()
        row = Document(
            id=uuid7(),
            organization_id=organization_id,
            title=title or "Untitled",
            source=document.source,
            mime_type=str(extracted.format),
            content_hash=content_hash,
            content=text,
            byte_size=byte_size,
            char_count=len(text),
            token_count=sum(chunk.token_count for chunk in chunks),
            chunk_count=len(chunks),
            embedding_space=self.embedder.space if embedded else None,
            ingestion_ms=0,
            embedding_ms=batch.latency_ms,
            metadata_=document.metadata,
        )
        await self._insert_document(row, organization_id)

        chunk_rows: list[DocumentChunk] = []
        embedding_rows: list[Embedding] = []
        for chunk, vector in zip(chunks, batch.vectors, strict=True):
            chunk_row = DocumentChunk(
                id=uuid7(),
                document_id=row.id,
                organization_id=organization_id,
                chunk_index=chunk.index,
                content=chunk.content,
                token_count=chunk.token_count,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                section=chunk.section[:1024] if chunk.section else None,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                embedding=to_index_vector(vector) if vector is not None else None,
                embedding_space=self.embedder.space if vector is not None else None,
            )
            chunk_rows.append(chunk_row)
            if vector is not None:
                embedding_rows.append(
                    Embedding(
                        chunk_id=chunk_row.id,
                        provider=provider.name,
                        model=provider.model,
                        dimensions=len(vector),
                        vector=vector,
                    )
                )
        await self.chunks.add_many(chunk_rows, embedding_rows)
        store_ms = _elapsed_ms(stage)

        row.ingestion_ms = _elapsed_ms(started)
        row.ingestion_stats = {
            "format": extracted.format.name.lower(),
            "pages": extracted.page_count,
            "extract_ms": extract_ms,
            "chunk_ms": chunk_ms,
            "embed_ms": batch.latency_ms,
            "store_ms": store_ms,
            "embedding_tokens": batch.tokens,
            "embedding_batches": batch.batches,
            "embedded_chunks": embedded,
            "chunk_size": chunking.chunk_size,
            "chunk_overlap": chunking.chunk_overlap,
        }
        await self.session.flush()
        return row

    async def _insert_document(self, row: Document, organization_id: uuid.UUID) -> None:
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            # Lost a race with a concurrent upload of the same content.
            existing = await self.documents.get_by_content_hash(organization_id, row.content_hash)
            if existing is None:
                raise
            raise DuplicateDocumentError(existing.id) from None


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
