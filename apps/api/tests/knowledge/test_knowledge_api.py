import json
import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.core.config import Settings
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.embedding import Embedding
from cortex_api.models.knowledge_query import KnowledgeQuery

from ..conftest import OrganizationFactory
from .samples import docx_paragraph, make_docx, make_pdf

pytestmark = [pytest.mark.database, pytest.mark.redis]

MAX_UPLOAD = 32_768

HANDBOOK = """# Handbook

## Leave
Employees accrue 25 days of paid leave per year. Unused leave carries over up to 5 days.

## Expenses
Submit expense reports within 30 days. Receipts are required above 50 USD.
"""


@pytest.fixture
def app_settings(redis_key_prefix: str) -> Settings:
    return Settings(
        env="test",
        memory_session_key_prefix=redis_key_prefix,
        knowledge_cache_key_prefix=redis_key_prefix,
        rate_limit_key_prefix=redis_key_prefix,
        knowledge_max_upload_bytes=MAX_UPLOAD,
        _env_file=None,
    )


async def create(api: httpx2.AsyncClient, headers: dict[str, str], **body: Any) -> dict[str, Any]:
    payload = {"title": "Handbook", "content": HANDBOOK, "mime_type": "text/markdown", **body}
    response = await api.post("/v1/documents", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def upload(
    api: httpx2.AsyncClient,
    headers: dict[str, str],
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
    **form: str,
) -> httpx2.Response:
    return await api.post(
        "/v1/documents/ingest",
        files={"file": (filename, data, content_type)},
        data=form,
        headers=headers,
    )


class TestCreate:
    async def test_inline_markdown(
        self, api: httpx2.AsyncClient, headers: dict[str, str], session: AsyncSession
    ) -> None:
        document = await create(api, headers, source="wiki://handbook", metadata={"team": "people"})
        assert document["mime_type"] == "text/markdown"
        assert document["source"] == "wiki://handbook"
        assert document["metadata"] == {"team": "people"}
        assert document["chunk_count"] >= 1 and document["token_count"] > 0
        assert document["embedding_space"] == "local/hashing-v1@1536"
        stages = document["ingestion"]["stages"]
        assert stages["format"] == "markdown"
        assert stages["embedded_chunks"] == document["chunk_count"]
        assert document["ingestion"]["ingestion_ms"] >= 0

        chunk_rows = await session.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_id == uuid.UUID(document["id"]))
        )
        assert chunk_rows == document["chunk_count"]

    async def test_chunking_override(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        text = " ".join(f"Sentence number {n} describes the policy." for n in range(40))
        default = await create(api, headers, title="Default", content=text)
        # Deduplication is by content, so the second copy must differ.
        small = await create(
            api, headers, title="Small", content=text + " End.", chunking={"chunk_size": 32}
        )
        assert small["chunk_count"] > default["chunk_count"]
        assert small["ingestion"]["stages"]["chunk_size"] == 32
        assert small["ingestion"]["stages"]["chunk_overlap"] == 8

    @pytest.mark.parametrize(
        "body",
        [
            {"title": "x", "content": "   "},
            {"title": "", "content": "text"},
            {"title": "x", "content": "text", "mime_type": "application/pdf"},
            {"title": "x", "content": "text", "chunking": {"chunk_size": 64, "chunk_overlap": 64}},
            {"title": "x", "content": "text", "chunking": {"chunk_overlap": 600}},
            {"title": "x", "content": "text", "metadata": {"blob": "x" * 20_000}},
            {"title": "x", "content": "text", "unknown": 1},
        ],
    )
    async def test_invalid_bodies(
        self, api: httpx2.AsyncClient, headers: dict[str, str], body: dict[str, Any]
    ) -> None:
        response = await api.post("/v1/documents", json=body, headers=headers)
        assert response.status_code == 422, response.text

    async def test_duplicates_are_rejected_with_the_existing_id(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        first = await create(api, headers)
        response = await api.post(
            "/v1/documents",
            json={"title": "Copy", "content": HANDBOOK, "mime_type": "text/markdown"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["document_id"] == first["id"]


class TestUpload:
    async def test_markdown(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        response = await upload(
            api,
            headers,
            "handbook.md",
            HANDBOOK.encode(),
            "text/markdown",
            metadata=json.dumps({"team": "people"}),
            chunk_size="64",
        )
        assert response.status_code == 201, response.text
        document = response.json()
        assert document["title"] == "Handbook"
        assert document["source"] == "handbook.md"
        assert document["metadata"] == {"team": "people"}
        assert document["ingestion"]["stages"]["chunk_size"] == 64

    async def test_pdf(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        pdf = make_pdf(
            [["Travel policy overview."], ["Flights over six hours may be booked in business."]],
            title="Travel Policy",
        )
        response = await upload(api, headers, "travel.pdf", pdf)
        assert response.status_code == 201, response.text
        document = response.json()
        assert document["title"] == "Travel Policy"
        assert document["mime_type"] == "application/pdf"
        assert document["ingestion"]["stages"]["pages"] == 2

        detail = await api.get(
            f"/v1/documents/{document['id']}", params={"include_chunks": True}, headers=headers
        )
        chunks = detail.json()["chunks"]
        assert chunks[0]["page_start"] == 1 and chunks[-1]["page_end"] == 2

    async def test_docx_title_override(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        docx = make_docx(
            [docx_paragraph("Onboarding", style="Heading1"), docx_paragraph("Day one: laptop.")],
            title="Original",
        )
        response = await upload(api, headers, "onboarding.docx", docx, title="Onboarding Guide")
        assert response.status_code == 201, response.text
        assert response.json()["title"] == "Onboarding Guide"
        assert response.json()["mime_type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    @pytest.mark.parametrize(
        ("filename", "data", "form", "status"),
        [
            ("image.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, {}, 415),
            ("empty.txt", b"", {}, 422),
            ("blank.txt", b"   \n\n  ", {}, 422),
            ("big.txt", b"a " * (MAX_UPLOAD // 2 + 1), {}, 413),
            ("fake.pdf", b"%PDF-1.4 not really a pdf", {}, 422),
            ("a.txt", b"hello world", {"metadata": "{not json"}, 422),
            ("a.txt", b"hello world", {"metadata": "[1, 2]"}, 422),
            ("a.txt", b"hello world", {"separators": '[""]'}, 422),
            ("a.txt", b"hello world", {"chunk_size": "64", "chunk_overlap": "64"}, 422),
            ("a.txt", b"hello world", {"chunk_size": "8"}, 422),
        ],
    )
    async def test_rejections(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        filename: str,
        data: bytes,
        form: dict[str, str],
        status: int,
    ) -> None:
        response = await upload(api, headers, filename, data, **form)
        assert response.status_code == status, response.text


class TestReadAndDelete:
    async def test_list_paginates_and_filters(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        ids = [
            (
                await create(
                    api, headers, title=f"Doc {n}", content=f"Note number {n}.", source=f"s{n % 2}"
                )
            )["id"]
            for n in range(3)
        ]
        page = (await api.get("/v1/documents", params={"limit": 2}, headers=headers)).json()
        assert page["total"] == 3 and page["limit"] == 2
        assert [item["id"] for item in page["items"]] == ids[::-1][:2]
        rest = (
            await api.get("/v1/documents", params={"limit": 2, "offset": 2}, headers=headers)
        ).json()
        assert [item["id"] for item in rest["items"]] == [ids[0]]

        by_source = (
            await api.get("/v1/documents", params={"source": "s0"}, headers=headers)
        ).json()
        assert {item["id"] for item in by_source["items"]} == {ids[0], ids[2]}
        by_type = (
            await api.get("/v1/documents", params={"mime_type": "text/plain"}, headers=headers)
        ).json()
        assert by_type["total"] == 0

    async def test_tenant_isolation(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        organization_factory: OrganizationFactory,
    ) -> None:
        document = await create(api, headers)
        other = {ORGANIZATION_HEADER: str((await organization_factory()).id)}
        assert (await api.get("/v1/documents", headers=other)).json()["total"] == 0
        assert (await api.get(f"/v1/documents/{document['id']}", headers=other)).status_code == 404
        assert (
            await api.delete(f"/v1/documents/{document['id']}", headers=other)
        ).status_code == 404
        search = await api.post("/v1/knowledge/search", json={"query": "leave"}, headers=other)
        assert search.json()["results"] == []
        # The same content is not a duplicate in another organization.
        again = await api.post(
            "/v1/documents",
            json={"title": "Handbook", "content": HANDBOOK, "mime_type": "text/markdown"},
            headers=other,
        )
        assert again.status_code == 201

    async def test_detail_with_chunk_pages(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        content = HANDBOOK + "".join(
            f"\n## Topic {n}\nDetails about topic {n}.\n" for n in range(4)
        )
        document = await create(
            api, headers, content=content, chunking={"chunk_size": 32, "chunk_overlap": 0}
        )
        assert document["chunk_count"] >= 4
        url = f"/v1/documents/{document['id']}"
        plain = (await api.get(url, headers=headers)).json()
        assert plain["chunks"] is None
        chunks = (
            await api.get(
                url,
                params={"include_chunks": True, "chunk_limit": 2, "chunk_offset": 1},
                headers=headers,
            )
        ).json()["chunks"]
        assert [c["chunk_index"] for c in chunks] == [1, 2]
        assert chunks[0]["section"].startswith("Handbook > ")
        assert (
            content[chunks[0]["char_start"] : chunks[0]["char_end"]].strip()
            == chunks[0]["content"].strip()
        )
        assert chunks[0]["embedding_space"] == "local/hashing-v1@1536"

    async def test_delete_cascades(
        self, api: httpx2.AsyncClient, headers: dict[str, str], session: AsyncSession
    ) -> None:
        document = await create(api, headers)
        document_id = uuid.UUID(document["id"])
        chunk_ids = select(DocumentChunk.id).where(DocumentChunk.document_id == document_id)
        assert await session.scalar(
            select(func.count()).select_from(Embedding).where(Embedding.chunk_id.in_(chunk_ids))
        )
        response = await api.delete(f"/v1/documents/{document_id}", headers=headers)
        assert response.status_code == 204
        assert (await api.get(f"/v1/documents/{document_id}", headers=headers)).status_code == 404
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Embedding).where(Embedding.chunk_id.in_(chunk_ids))
            )
            == 0
        )


class TestSearch:
    async def test_search_returns_results_context_and_metrics(
        self, api: httpx2.AsyncClient, headers: dict[str, str], session: AsyncSession
    ) -> None:
        document = await create(api, headers, metadata={"team": "people"})
        await create(api, headers, title="Other", content="Kubernetes pods restart on failure.")
        response = await api.post(
            "/v1/knowledge/search",
            json={
                "query": "how many days of paid leave",
                "top_k": 3,
                "filters": {"metadata": {"team": "people"}},
            },
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "hybrid"
        assert body["results"] and {r["document_id"] for r in body["results"]} == {document["id"]}
        top = body["results"][0]
        assert top["section"] == "Handbook > Leave"
        assert top["citation"] == 1
        citation = body["context"]["citations"][0]
        assert citation["label"] == "Handbook > Leave"
        assert citation["cited"] is None
        assert body["context"]["text"].startswith("[1] Handbook > Leave\n")
        assert body["context"]["confidence"]["level"] in {"high", "medium", "low"}
        metrics = body["metrics"]
        assert metrics["embedding_space"] == "local/hashing-v1@1536"
        assert "leav" in metrics["query_terms"] or "leave" in metrics["query_terms"]
        assert metrics["warnings"] == []

        logged = await session.get(KnowledgeQuery, uuid.UUID(body["query_id"]))
        assert logged is not None
        assert logged.filters == {"metadata": {"team": "people"}}
        assert logged.citation_count == len(body["context"]["citations"])

    async def test_second_search_uses_the_cache(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        await create(api, headers)
        payload = {"query": "expense receipts"}
        first = (await api.post("/v1/knowledge/search", json=payload, headers=headers)).json()
        second = (await api.post("/v1/knowledge/search", json=payload, headers=headers)).json()
        assert first["metrics"]["query_embedding_cached"] is False
        assert second["metrics"]["query_embedding_cached"] is True

    @pytest.mark.parametrize(
        "body",
        [
            {"query": ""},
            {"query": "  "},
            {"query": "x" * 2001},
            {"query": "x", "top_k": 0},
            {"query": "x", "mode": "fuzzy"},
            {"query": "x", "recency_weight": 2},
            {"query": "x", "filters": {"unknown": 1}},
        ],
    )
    async def test_invalid_search(
        self, api: httpx2.AsyncClient, headers: dict[str, str], body: dict[str, Any]
    ) -> None:
        response = await api.post("/v1/knowledge/search", json=body, headers=headers)
        assert response.status_code == 422

    async def test_metrics(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        await create(api, headers)
        await api.post("/v1/knowledge/search", json={"query": "paid leave"}, headers=headers)
        await api.post("/v1/knowledge/search", json={"query": "zzzqqq"}, headers=headers)
        response = await api.get(
            "/v1/knowledge/metrics", params={"window_hours": 1}, headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["window_hours"] == 1
        assert body["corpus"]["documents"] == 1 and body["corpus"]["ingested"] == 1
        assert body["corpus"]["chunks"] >= 1
        retrieval = body["retrieval"]
        assert retrieval["queries"] == 2
        assert retrieval["p95_ms"] is not None
        assert retrieval["grounded_completions"] == 0
        assert retrieval["citation_usage_rate"] is None
        assert 0 <= retrieval["zero_result_rate"] <= 1
        bad = await api.get("/v1/knowledge/metrics", params={"window_hours": 0}, headers=headers)
        assert bad.status_code == 422
