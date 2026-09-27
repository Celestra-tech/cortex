import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.models.document import Document
from cortex_api.models.document_chunk import EMBEDDING_DIMENSIONS, DocumentChunk
from cortex_api.models.embedding import Embedding
from cortex_api.models.organization import Organization
from cortex_api.services.knowledge.chunker import ChunkingConfig
from cortex_api.services.knowledge.embeddings import EmbeddingTask
from cortex_api.services.knowledge.extraction import DocumentFormat, ExtractionError
from cortex_api.services.knowledge.ingestion import (
    DocumentInput,
    DocumentTooLargeError,
    DuplicateDocumentError,
    IngestionLimits,
    IngestionPipeline,
)
from cortex_api.services.knowledge.service import KnowledgeService
from cortex_api.services.router.base import ProviderError

from ..conftest import OrganizationFactory
from .conftest import CORPUS, DocumentFactory, RecordingProvider
from .samples import docx_paragraph, make_docx, make_pdf

pytestmark = pytest.mark.database


async def count(session: AsyncSession, model: type[object]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_text_pipeline_stores_document_chunks_and_embeddings(
    session: AsyncSession,
    add_document: DocumentFactory,
    recording_provider: RecordingProvider,
) -> None:
    content, metadata = CORPUS["Refund Policy"]
    document = await add_document("Refund Policy", content, metadata=metadata)

    assert document.mime_type == "text/markdown"
    assert document.metadata_ == metadata
    assert document.source == "kb://Refund Policy"
    assert document.char_count == len(content)
    assert len(document.content_hash) == 64
    assert document.embedding_space == "recording/rec-1@64"
    assert document.chunk_count == 2
    assert document.ingestion_ms >= document.embedding_ms >= 0
    stats = document.ingestion_stats
    assert stats["format"] == "markdown"
    assert stats["embedded_chunks"] == 2
    assert stats["chunk_size"] == 512
    assert set(stats) >= {"extract_ms", "chunk_ms", "embed_ms", "store_ms", "embedding_tokens"}

    chunks = (
        (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert [c.section for c in chunks] == ["Refunds", "Chargebacks"]
    assert document.token_count == sum(c.token_count for c in chunks)
    assert all(c.embedding_space == document.embedding_space for c in chunks)
    assert all(c.organization_id == document.organization_id for c in chunks)

    indexed = (
        await session.execute(
            select(DocumentChunk.embedding).where(DocumentChunk.id == chunks[0].id)
        )
    ).scalar_one()
    assert indexed is not None and len(indexed) == EMBEDDING_DIMENSIONS
    assert all(value == 0.0 for value in indexed[64:])  # zero-padded beyond native width

    ledger = (
        await session.execute(select(Embedding).where(Embedding.chunk_id == chunks[0].id))
    ).scalar_one()
    assert (ledger.provider, ledger.model, ledger.dimensions) == ("recording", "rec-1", 64)
    native = (
        await session.execute(select(Embedding.vector).where(Embedding.id == ledger.id))
    ).scalar_one()
    assert native == pytest.approx(indexed[:64], abs=1e-6)

    # Embedded text carries the title and heading path for context.
    task, texts = recording_provider.inputs[0]
    assert task is EmbeddingTask.DOCUMENT
    assert texts[0].startswith("Refund Policy > Refunds\n\n# Refunds")


async def test_pdf_upload_keeps_pages_and_title(
    session: AsyncSession, knowledge: KnowledgeService, organization: Organization
) -> None:
    pdf = make_pdf(
        [
            ["Travel policy", "Economy class for flights under six hours."],
            ["Hotels up to 200 EUR."],
        ],
        title="Travel Policy 2026",
    )
    document = await knowledge.ingest_file(
        organization.id, pdf, DocumentInput(), filename="travel.pdf", content_type="application/pdf"
    )
    assert document.title == "Travel Policy 2026"
    assert document.mime_type == "application/pdf"
    assert document.byte_size == len(pdf)
    assert document.ingestion_stats["pages"] == 2
    [chunk] = (
        (
            await session.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert (chunk.page_start, chunk.page_end) == (1, 2)


async def test_docx_upload_uses_filename_when_untitled(
    knowledge: KnowledgeService, organization: Organization
) -> None:
    docx = make_docx([docx_paragraph("Onboarding", style="Heading1"), docx_paragraph("Day one.")])
    document = await knowledge.ingest_file(
        organization.id, docx, DocumentInput(), filename="onboarding-guide.docx"
    )
    assert document.title == "onboarding-guide"
    assert document.mime_type == DocumentFormat.DOCX.value


async def test_duplicates_are_rejected_per_organization(
    add_document: DocumentFactory, organization_factory: OrganizationFactory
) -> None:
    original = await add_document("A", "Same words.\n")
    with pytest.raises(DuplicateDocumentError) as caught:
        await add_document("B", "Same words.")  # identical after normalization
    assert caught.value.document_id == original.id
    other = await organization_factory()
    assert (await add_document("A", "Same words.", organization_id=other.id)).id != original.id


async def test_embedding_failure_persists_nothing(
    session: AsyncSession,
    add_document: DocumentFactory,
    recording_provider: RecordingProvider,
) -> None:
    recording_provider.fail()
    with pytest.raises(ProviderError):
        await add_document("Doomed", "This will never be stored.")
    assert await count(session, Document) == 0
    assert await count(session, DocumentChunk) == 0


async def test_chunks_without_words_are_stored_unembedded(add_document: DocumentFactory) -> None:
    # The embedded text includes the title, so it must be word-free too.
    document = await add_document("???", "!!! ??? ...", fmt=DocumentFormat.TEXT)
    assert document.chunk_count == 1
    assert document.embedding_space is None
    assert document.ingestion_stats["embedded_chunks"] == 0


async def test_limits(
    session: AsyncSession,
    app_settings: Settings,
    organization: Organization,
    knowledge: KnowledgeService,
) -> None:
    pipeline = IngestionPipeline(
        session,
        knowledge.pipeline.embedder,
        chunking=ChunkingConfig(chunk_size=32, chunk_overlap=0),
        limits=IngestionLimits(max_upload_bytes=200, max_document_chars=150, max_chunks=1),
    )
    with pytest.raises(DocumentTooLargeError, match="bytes"):
        await pipeline.ingest_file(organization.id, b"x" * 201, DocumentInput(), filename="a.txt")
    with pytest.raises(DocumentTooLargeError, match="characters"):
        await pipeline.ingest_text(organization.id, "word " * 40, DocumentInput(title="t"))
    with pytest.raises(DocumentTooLargeError, match="chunks"):
        # 144 characters is about 36 tokens: two 32-token chunks.
        await pipeline.ingest_text(
            organization.id, "Alpha beta gamma delta. " * 6, DocumentInput(title="t")
        )
    with pytest.raises(ExtractionError, match="empty"):
        await pipeline.ingest_file(organization.id, b"", DocumentInput(), filename="a.txt")


async def test_per_document_chunking_override(
    session: AsyncSession, knowledge: KnowledgeService, organization: Organization
) -> None:
    content = " ".join(f"Sentence {i} is here." for i in range(60))
    document = await knowledge.ingest_text(
        organization.id,
        content,
        DocumentInput(title="Long", chunking=ChunkingConfig(chunk_size=40, chunk_overlap=8)),
    )
    assert document.chunk_count > 5
    assert document.ingestion_stats["chunk_size"] == 40
    sizes = (
        (
            await session.execute(
                select(DocumentChunk.token_count).where(DocumentChunk.document_id == document.id)
            )
        )
        .scalars()
        .all()
    )
    assert max(sizes) <= 40


async def test_constraints(
    session: AsyncSession, add_document: DocumentFactory, organization: Organization
) -> None:
    document = await add_document("Doc", "Body text.")
    bad = DocumentChunk(
        document_id=document.id,
        organization_id=organization.id,
        chunk_index=1,
        content="x",
        token_count=1,
        char_start=0,
        char_end=1,
        embedding=None,
        embedding_space="orphan-space",
    )
    session.add(bad)
    with pytest.raises(IntegrityError, match="embedding_space_iff_embedding"):
        async with session.begin_nested():
            await session.flush()
