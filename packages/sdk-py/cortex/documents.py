from __future__ import annotations

import json
import mimetypes
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import IO, Any

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from ._validation import invalid, validate_params
from .models import (
    ChunkingOptions,
    Document,
    DocumentDetail,
    DocumentParams,
    IngestParams,
    Metadata,
    Page,
    TextMimeType,
)

DocumentPage = Page[Document]
Uploadable = str | os.PathLike[str] | bytes | IO[bytes]
"""A path, raw bytes, or a binary file object."""

# Ingestion parses, chunks, and embeds before answering; large PDFs take a while.
UPLOAD_TIMEOUT = 120.0


def _drop_none(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


def _read(file: Uploadable, filename: str | None) -> tuple[str | None, bytes]:
    if isinstance(file, bytes):
        return filename, file
    if isinstance(file, str | os.PathLike):
        path = Path(file)
        return filename or path.name, path.read_bytes()
    name = getattr(file, "name", None)
    inferred = Path(name).name if isinstance(name, str) else None
    return filename or inferred, file.read()


def _upload(
    file: Uploadable,
    filename: str | None,
    title: str | None,
    source: str | None,
    metadata: Metadata | None,
    chunking: ChunkingOptions | None,
    options: RequestOptions | None,
) -> Call:
    op = "documents.upload"
    params = validate_params(
        op,
        IngestParams,
        _drop_none(title=title, source=source, metadata=metadata, chunking=chunking),
    )
    name, content = _read(file, filename)
    if not name:
        raise invalid(op, "filename", "required when uploading bytes or an unnamed stream")
    form: dict[str, str] = {}
    for key in ("title", "source"):
        if key in params:
            form[key] = params[key]
    if "metadata" in params:
        form["metadata"] = json.dumps(params["metadata"])
    chunks = params.get("chunking") or {}
    for key in ("chunk_size", "chunk_overlap"):
        if key in chunks:
            form[key] = str(chunks[key])
    if "separators" in chunks:
        form["separators"] = json.dumps(chunks["separators"])
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Call(
        operation=op,
        method="POST",
        path="/v1/documents/ingest",
        files={"file": (name, content, mime)},
        data=form,
        default_timeout=UPLOAD_TIMEOUT,
        options=options,
    )


def _create(params: dict[str, Any], options: RequestOptions | None) -> Call:
    op = "documents.create"
    return Call(
        operation=op,
        method="POST",
        path="/v1/documents",
        json=validate_params(op, DocumentParams, params),
        default_timeout=UPLOAD_TIMEOUT,
        options=options,
    )


def _list(
    source: str | None,
    mime_type: str | None,
    limit: int | None,
    offset: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="documents.list",
        path="/v1/documents",
        params={"source": source, "mime_type": mime_type, "limit": limit, "offset": offset},
        options=options,
    )


def _get(
    id: str,
    include_chunks: bool,
    chunk_limit: int | None,
    chunk_offset: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="documents.get",
        path=f"/v1/documents/{segment(id)}",
        params={
            "include_chunks": include_chunks or None,
            "chunk_limit": chunk_limit,
            "chunk_offset": chunk_offset,
        },
        options=options,
    )


def _delete(id: str, options: RequestOptions | None) -> Call:
    return Call(
        operation="documents.delete",
        method="DELETE",
        path=f"/v1/documents/{segment(id)}",
        options=options,
    )


class Documents(SyncAPIResource):
    """The knowledge corpus. Uploading identical content twice raises
    `ConflictError`, whose `document_id` is the existing copy."""

    def upload(
        self,
        file: Uploadable,
        *,
        filename: str | None = None,
        title: str | None = None,
        source: str | None = None,
        metadata: Metadata | None = None,
        chunking: ChunkingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Document:
        """Ingests a PDF, DOCX, Markdown, or text file.

        The format is detected from `filename`, which defaults to the path's or
        file object's name and is required for raw bytes. `title` defaults to
        the document's own title, then the file name.
        """
        call = _upload(file, filename, title, source, metadata, chunking, request_options)
        return self._transport.request(call, Document)

    def create(
        self,
        *,
        title: str,
        content: str,
        source: str | None = None,
        mime_type: TextMimeType | None = None,
        metadata: Metadata | None = None,
        chunking: ChunkingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Document:
        """Ingests inline text or Markdown."""
        params = _drop_none(
            title=title,
            content=content,
            source=source,
            mime_type=mime_type,
            metadata=metadata,
            chunking=chunking,
        )
        return self._transport.request(_create(params, request_options), Document)

    def list(
        self,
        *,
        source: str | None = None,
        mime_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DocumentPage:
        call = _list(source, mime_type, limit, offset, request_options)
        return self._transport.request(call, DocumentPage)

    def iter(
        self, *, source: str | None = None, mime_type: str | None = None, page_size: int = 100
    ) -> Iterator[Document]:
        """Every document, fetched page by page as you iterate."""
        return paginate(
            lambda limit, offset: self.list(
                source=source, mime_type=mime_type, limit=limit, offset=offset
            ),
            limit=page_size,
            offset=0,
        )

    def get(
        self,
        id: str,
        *,
        include_chunks: bool = False,
        chunk_limit: int | None = None,
        chunk_offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DocumentDetail:
        call = _get(id, include_chunks, chunk_limit, chunk_offset, request_options)
        return self._transport.request(call, DocumentDetail)

    def delete(self, id: str, *, request_options: RequestOptions | None = None) -> None:
        """Permanently removes the document, its chunks, and their embeddings."""
        self._transport.send(_delete(id, request_options))


class AsyncDocuments(AsyncAPIResource):
    async def upload(
        self,
        file: Uploadable,
        *,
        filename: str | None = None,
        title: str | None = None,
        source: str | None = None,
        metadata: Metadata | None = None,
        chunking: ChunkingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Document:
        call = _upload(file, filename, title, source, metadata, chunking, request_options)
        return await self._transport.request(call, Document)

    async def create(
        self,
        *,
        title: str,
        content: str,
        source: str | None = None,
        mime_type: TextMimeType | None = None,
        metadata: Metadata | None = None,
        chunking: ChunkingOptions | None = None,
        request_options: RequestOptions | None = None,
    ) -> Document:
        params = _drop_none(
            title=title,
            content=content,
            source=source,
            mime_type=mime_type,
            metadata=metadata,
            chunking=chunking,
        )
        return await self._transport.request(_create(params, request_options), Document)

    async def list(
        self,
        *,
        source: str | None = None,
        mime_type: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DocumentPage:
        call = _list(source, mime_type, limit, offset, request_options)
        return await self._transport.request(call, DocumentPage)

    def iter(
        self, *, source: str | None = None, mime_type: str | None = None, page_size: int = 100
    ) -> AsyncIterator[Document]:
        return apaginate(
            lambda limit, offset: self.list(
                source=source, mime_type=mime_type, limit=limit, offset=offset
            ),
            limit=page_size,
            offset=0,
        )

    async def get(
        self,
        id: str,
        *,
        include_chunks: bool = False,
        chunk_limit: int | None = None,
        chunk_offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DocumentDetail:
        call = _get(id, include_chunks, chunk_limit, chunk_offset, request_options)
        return await self._transport.request(call, DocumentDetail)

    async def delete(self, id: str, *, request_options: RequestOptions | None = None) -> None:
        await self._transport.send(_delete(id, request_options))
