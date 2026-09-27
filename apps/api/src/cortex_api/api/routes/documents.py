import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from pydantic import ValidationError

from cortex_api.api.deps import KnowledgeServiceDep, OrganizationDep, SettingsDep
from cortex_api.repositories.knowledge_repository import DocumentFilters, DocumentRepository
from cortex_api.schemas.knowledge import (
    ChunkingOptions,
    ChunkRead,
    DocumentCreate,
    DocumentDetail,
    DocumentListResponse,
    DocumentRead,
    check_metadata_size,
)
from cortex_api.services.knowledge.chunker import ChunkingConfig
from cortex_api.services.knowledge.extraction import DocumentFormat
from cortex_api.services.knowledge.ingestion import DocumentInput

router = APIRouter(prefix="/documents", tags=["documents"])


def _chunking(options: ChunkingOptions | None, defaults: ChunkingConfig) -> ChunkingConfig | None:
    if options is None:
        return None
    try:
        return options.resolve(defaults)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"chunking: {exc}") from exc


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def create_document(
    body: DocumentCreate, organization: OrganizationDep, knowledge: KnowledgeServiceDep
) -> DocumentRead:
    """Ingests inline text or Markdown through the full pipeline."""
    document = await knowledge.ingest_text(
        organization.id,
        body.content,
        DocumentInput(
            title=body.title,
            source=body.source,
            metadata=body.metadata,
            chunking=_chunking(body.chunking, knowledge.default_chunking),
        ),
        fmt=DocumentFormat(body.mime_type),
    )
    return DocumentRead.from_document(document)


def _json_form(name: str, raw: str | None) -> Any:
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} must be valid JSON"
        ) from exc


@router.post("/ingest", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def ingest_document(
    organization: OrganizationDep,
    knowledge: KnowledgeServiceDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File(description="PDF, DOCX, Markdown, or plain text")],
    title: Annotated[str | None, Form(max_length=512)] = None,
    source: Annotated[str | None, Form(max_length=2048)] = None,
    metadata: Annotated[str | None, Form(description="JSON object")] = None,
    chunk_size: Annotated[int | None, Form(ge=32, le=8192)] = None,
    chunk_overlap: Annotated[int | None, Form(ge=0, le=2048)] = None,
    separators: Annotated[str | None, Form(description="JSON array of strings")] = None,
) -> DocumentRead:
    """Uploads a file and runs extract -> normalize -> chunk -> embed -> store."""
    parsed_metadata = _json_form("metadata", metadata) or {}
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "metadata must be an object")
    try:
        check_metadata_size(parsed_metadata)
        options = (
            ChunkingOptions(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=_json_form("separators", separators),
            )
            if chunk_size is not None or chunk_overlap is not None or separators
            else None
        )
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    # Read one byte past the limit so oversize uploads are rejected without buffering them whole.
    data = await file.read(settings.knowledge_max_upload_bytes + 1)
    document = await knowledge.ingest_file(
        organization.id,
        data,
        DocumentInput(
            title=title,
            source=source or file.filename,
            metadata=parsed_metadata,
            chunking=_chunking(options, knowledge.default_chunking),
        ),
        filename=file.filename,
        content_type=file.content_type,
    )
    return DocumentRead.from_document(document)


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    organization: OrganizationDep,
    knowledge: KnowledgeServiceDep,
    source: Annotated[str | None, Query(max_length=2048)] = None,
    mime_type: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=DocumentRepository.max_limit)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    """Newest first."""
    filters = DocumentFilters(
        sources=(source,) if source else None, mime_types=(mime_type,) if mime_type else None
    )
    items, total = await knowledge.list_documents(
        organization.id, filters, limit=limit, offset=offset
    )
    return DocumentListResponse(
        items=[DocumentRead.from_document(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    organization: OrganizationDep,
    knowledge: KnowledgeServiceDep,
    include_chunks: bool = False,
    chunk_limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    chunk_offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentDetail:
    document = await knowledge.get_document(organization.id, document_id)
    detail = DocumentDetail(**DocumentRead.from_document(document).model_dump())
    if include_chunks:
        chunks = await knowledge.list_chunks(document, limit=chunk_limit, offset=chunk_offset)
        detail.chunks = [ChunkRead.from_chunk(chunk) for chunk in chunks]
    return detail


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID, organization: OrganizationDep, knowledge: KnowledgeServiceDep
) -> Response:
    """Permanently removes the document, its chunks, and their embeddings."""
    await knowledge.delete_document(organization.id, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
