from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from cortex import AsyncCortex, Cortex, ValidationError

from .helpers import Server, json_response

CITATION = {
    "index": 1,
    "document_id": "d1",
    "chunk_ids": ["ch1"],
    "title": "Refund policy",
    "source": "wiki",
    "section": "Timing",
    "page_start": None,
    "page_end": None,
    "label": "Refund policy > Timing",
    "score": 0.91,
    "snippet": "Refunds post within 5 business days.",
    "cited": None,
}
SEARCH: dict[str, Any] = {
    "query_id": "q1",
    "query": "how long do refunds take",
    "mode": "hybrid",
    "results": [
        {
            "chunk_id": "ch1",
            "document_id": "d1",
            "title": "Refund policy",
            "source": "wiki",
            "section": "Timing",
            "chunk_index": 0,
            "page_start": None,
            "page_end": None,
            "content": "Refunds post within 5 business days.",
            "score": 0.91,
            "vector_similarity": 0.88,
            "keyword_score": 0.4,
            "vector_rank": 1,
            "keyword_rank": 1,
            "recency": 0.99,
            "citation": 1,
        }
    ],
    "context": {
        "text": "[1] Refund policy > Timing\nRefunds post within 5 business days.",
        "token_count": 14,
        "truncated": False,
        "citations": [CITATION],
        "confidence": {
            "score": 0.84,
            "level": "high",
            "similarity": 0.88,
            "coverage": 1.0,
            "agreement": 0.7,
        },
    },
    "metrics": {"latency_ms": 12.5, "embedding_ms": 4.0, "query_terms": ["refund"]},
}


def test_search_with_a_query_string(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(SEARCH))
    result = make_client(server).knowledge.search("  how long do refunds take  ")

    assert server.last.method == "POST"
    assert server.last.url.path == "/v1/knowledge/search"
    assert server.body() == {"query": "how long do refunds take"}
    assert result.results[0].citation == 1
    assert result.context.confidence.level == "high"
    assert result.context.citations[0].label == "Refund policy > Timing"
    assert result.context.text.startswith("[1]")


def test_search_with_options(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response(SEARCH))
    make_client(server).knowledge.search(
        "refunds",
        top_k=3,
        mode="keyword",
        filters={"sources": ["wiki"], "metadata": {"team": "billing"}},
        recency_weight=0.2,
        max_context_tokens=500,
    )
    assert server.body() == {
        "query": "refunds",
        "top_k": 3,
        "mode": "keyword",
        "filters": {"sources": ["wiki"], "metadata": {"team": "billing"}},
        "recency_weight": 0.2,
        "max_context_tokens": 500,
    }


@pytest.mark.parametrize(
    ("query", "kwargs", "path"),
    [
        ("   ", {}, "query"),
        ("ok", {"top_k": 0}, "top_k"),
        ("ok", {"mode": "fuzzy"}, "mode"),
        ("ok", {"recency_weight": 1.5}, "recency_weight"),
    ],
)
def test_search_validates(
    make_client: Callable[..., Cortex], query: str, kwargs: dict[str, Any], path: str
) -> None:
    server = Server(json_response(SEARCH))
    with pytest.raises(ValidationError) as caught:
        make_client(server).knowledge.search(query, **kwargs)
    assert caught.value.issues[0].path == path
    assert server.requests == []


def test_metrics_and_query_log(make_client: Callable[..., Cortex]) -> None:
    metrics = {
        "window_hours": 48,
        "corpus": {"documents": 3, "chunks": 40, "tokens": 9000, "ingested": 1},
        "retrieval": {"queries": 12, "p95_ms": 30.0, "grounded_completions": 4},
    }
    query = {
        "id": "q1",
        "query": "refunds",
        "mode": "hybrid",
        "top_k": 8,
        "filters": {},
        "embedding_space": "openai/text-embedding-3-small@1536",
        "result_count": 1,
        "citation_count": 1,
        "context_tokens": 14,
        "latency_ms": 12.5,
        "confidence": 0.84,
        "completion_id": None,
        "cited": None,
        "created_at": "2026-09-27T12:00:00Z",
    }
    server = Server(
        json_response(metrics),
        json_response({"items": [query], "total": 1, "limit": 10, "offset": 0}),
        json_response(
            {**query, "results": [{"chunk_id": "ch1", "document_id": "d1", "score": 0.9}]}
        ),
    )
    cortex = make_client(server)

    got = cortex.knowledge.metrics(window_hours=48)
    assert got.retrieval.p95_ms == 30.0
    assert server.last.url.params["window_hours"] == "48"

    page = cortex.knowledge.list_queries(limit=10)
    assert page.items[0].embedding_space == "openai/text-embedding-3-small@1536"

    detail = cortex.knowledge.get_query("q1")
    assert detail.results[0].citation is None
    assert server.last.url.path == "/v1/knowledge/queries/q1"


async def test_async_search(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(SEARCH))
    result = await make_async_client(server).knowledge.search("refunds", top_k=2)
    assert result.query_id == "q1"
    assert server.body() == {"query": "refunds", "top_k": 2}
