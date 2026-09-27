from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from cortex import AsyncCortex, ConflictError, Cortex, ValidationError

from .helpers import Server, json_response

DOCUMENT: dict[str, Any] = {
    "id": "d1",
    "title": "Refund policy",
    "source": "policy.md",
    "mime_type": "text/markdown",
    "metadata": {"team": "billing"},
    "content_hash": "abc",
    "byte_size": 120,
    "char_count": 118,
    "token_count": 30,
    "chunk_count": 2,
    "embedding_space": "openai/text-embedding-3-small@1536",
    "ingestion": {"ingestion_ms": 85.0, "embedding_ms": 40.0, "stages": {"chunk_ms": 1}},
    "created_at": "2026-09-27T12:00:00Z",
}


def form_parts(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """Minimal multipart parser: field name to (filename, body)."""
    boundary = request.headers["content-type"].split("boundary=")[1].encode()
    parts: dict[str, tuple[str | None, bytes]] = {}
    for chunk in request.content.split(b"--" + boundary)[1:-1]:
        head, _, body = chunk.removeprefix(b"\r\n").removesuffix(b"\r\n").partition(b"\r\n\r\n")
        disposition = head.split(b"\r\n")[0].decode()
        name = disposition.split('name="')[1].split('"')[0]
        filename = (
            disposition.split('filename="')[1].split('"')[0] if "filename=" in disposition else None
        )
        parts[name] = (filename, body)
    return parts


def test_upload_a_path(make_client: Callable[..., Cortex], tmp_path: Path) -> None:
    path = tmp_path / "policy.md"
    path.write_text("# Refunds\n\nRefunds post within 5 business days.\n")
    server = Server(json_response(DOCUMENT, 201))

    document = make_client(server).documents.upload(
        path,
        title="Refund policy",
        metadata={"team": "billing"},
        chunking={"chunk_size": 256, "chunk_overlap": 32, "separators": ["\n\n"]},
    )

    assert document.chunk_count == 2
    assert document.ingestion.stages == {"chunk_ms": 1}
    request = server.last
    assert request.url.path == "/v1/documents/ingest"
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert request.extensions["timeout"]["read"] == 120
    parts = form_parts(request)
    assert parts["file"] == ("policy.md", path.read_bytes())
    assert parts["title"] == (None, b"Refund policy")
    assert json.loads(parts["metadata"][1]) == {"team": "billing"}
    assert parts["chunk_size"] == (None, b"256")
    assert parts["chunk_overlap"] == (None, b"32")
    assert json.loads(parts["separators"][1]) == ["\n\n"]
    assert "source" not in parts


def test_upload_bytes_requires_a_filename(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(DOCUMENT, 201))
    cortex = make_client(server)
    with pytest.raises(ValidationError) as caught:
        cortex.documents.upload(b"%PDF-1.7 ...")
    assert caught.value.issues[0].path == "filename"
    assert server.requests == []

    cortex.documents.upload(b"%PDF-1.7 ...", filename="report.pdf")
    assert form_parts(server.last)["file"] == ("report.pdf", b"%PDF-1.7 ...")


def test_upload_a_file_object(make_client: Callable[..., Cortex], tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_bytes(b"plain notes")
    server = Server(json_response(DOCUMENT, 201))
    with path.open("rb") as handle:
        make_client(server).documents.upload(handle, source="drive")
    parts = form_parts(server.last)
    assert parts["file"] == ("notes.txt", b"plain notes")
    assert parts["source"] == (None, b"drive")

    make_client(server).documents.upload(io.BytesIO(b"x"), filename="x.md")
    assert form_parts(server.last)["file"] == ("x.md", b"x")


def test_duplicate_upload_is_a_conflict(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({"detail": "Document already ingested", "document_id": "d1"}, 409)
    )
    with pytest.raises(ConflictError) as caught:
        make_client(server).documents.upload(b"same", filename="a.txt")
    assert caught.value.document_id == "d1"
    assert len(server.requests) == 1


def test_create_inline_document(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(DOCUMENT, 201))
    make_client(server).documents.create(
        title="Refund policy", content="Refunds post within 5 days.", mime_type="text/markdown"
    )
    assert server.last.url.path == "/v1/documents"
    assert server.body() == {
        "title": "Refund policy",
        "content": "Refunds post within 5 days.",
        "mime_type": "text/markdown",
    }


def test_create_validates(make_client: Callable[..., Cortex]) -> None:
    with pytest.raises(ValidationError, match="title"):
        make_client(Server(json_response(DOCUMENT))).documents.create(title="", content="x")


def test_list_iterate_get_and_delete(make_client: Callable[..., Cortex]) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.url.path == "/v1/documents/d1":
            return json_response({**DOCUMENT, "chunks": []})
        offset = int(request.url.params.get("offset", 0))
        return json_response(
            {"items": [{**DOCUMENT, "id": f"d{offset}"}], "total": 2, "limit": 1, "offset": offset}
        )

    server = Server(handle)
    cortex = make_client(server)

    page = cortex.documents.list(source="wiki", mime_type="text/markdown", limit=1)
    assert dict(server.last.url.params) == {
        "source": "wiki",
        "mime_type": "text/markdown",
        "limit": "1",
    }
    assert page.total == 2

    assert [d.id for d in cortex.documents.iter(source="wiki", page_size=1)] == ["d0", "d1"]

    detail = cortex.documents.get("d1", include_chunks=True, chunk_limit=5)
    assert detail.chunks == []
    assert dict(server.last.url.params) == {"include_chunks": "true", "chunk_limit": "5"}

    cortex.documents.get("d1")
    assert dict(server.last.url.params) == {}

    cortex.documents.delete("d1")
    assert server.last.method == "DELETE"


async def test_async_upload(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(DOCUMENT, 201))
    document = await make_async_client(server).documents.upload(b"hello", filename="hello.txt")
    assert document.id == "d1"
    assert form_parts(server.last)["file"] == ("hello.txt", b"hello")
