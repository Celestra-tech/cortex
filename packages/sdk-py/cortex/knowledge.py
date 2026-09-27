from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from ._validation import validate_params
from .models import (
    KnowledgeMetrics,
    KnowledgeQuery,
    KnowledgeQueryDetail,
    Page,
    SearchFilters,
    SearchMode,
    SearchParams,
    SearchResult,
)

QueryPage = Page[KnowledgeQuery]


def _search(
    query: str,
    top_k: int | None,
    mode: SearchMode | None,
    filters: SearchFilters | None,
    recency_weight: float | None,
    max_context_tokens: int | None,
    options: RequestOptions | None,
) -> Call:
    op = "knowledge.search"
    params: dict[str, Any] = {
        "query": query.strip(),
        "top_k": top_k,
        "mode": mode,
        "filters": filters,
        "recency_weight": recency_weight,
        "max_context_tokens": max_context_tokens,
    }
    body = validate_params(op, SearchParams, {k: v for k, v in params.items() if v is not None})
    return Call(
        operation=op,
        method="POST",
        path="/v1/knowledge/search",
        json=body,
        # Read-only despite POST, so safe to retry after timeouts and 5xx.
        idempotent=True,
        options=options,
    )


def _metrics(window_hours: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="knowledge.metrics",
        path="/v1/knowledge/metrics",
        params={"window_hours": window_hours},
        options=options,
    )


def _list_queries(limit: int | None, offset: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="knowledge.list_queries",
        path="/v1/knowledge/queries",
        params={"limit": limit, "offset": offset},
        options=options,
    )


def _get_query(id: str, options: RequestOptions | None) -> Call:
    return Call(
        operation="knowledge.get_query",
        path=f"/v1/knowledge/queries/{segment(id)}",
        options=options,
    )


class Knowledge(SyncAPIResource):
    """Hybrid retrieval over ingested documents, with citation-numbered context."""

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        mode: SearchMode | None = None,
        filters: SearchFilters | None = None,
        recency_weight: float | None = None,
        max_context_tokens: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> SearchResult:
        """`result.context.text` is ready to drop into a system prompt, and
        `result.context.citations` explains each `[n]` in it."""
        call = _search(
            query, top_k, mode, filters, recency_weight, max_context_tokens, request_options
        )
        return self._transport.request(call, SearchResult)

    def metrics(
        self, *, window_hours: int | None = None, request_options: RequestOptions | None = None
    ) -> KnowledgeMetrics:
        """Corpus size, ingestion and retrieval latency, and retrieval quality."""
        return self._transport.request(_metrics(window_hours, request_options), KnowledgeMetrics)

    def list_queries(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> QueryPage:
        """Logged retrievals, newest first."""
        return self._transport.request(_list_queries(limit, offset, request_options), QueryPage)

    def iter_queries(self, *, page_size: int = 100) -> Iterator[KnowledgeQuery]:
        return paginate(
            lambda limit, offset: self.list_queries(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    def get_query(
        self, id: str, *, request_options: RequestOptions | None = None
    ) -> KnowledgeQueryDetail:
        """One retrieval with its ranked results, for replaying citations."""
        return self._transport.request(_get_query(id, request_options), KnowledgeQueryDetail)


class AsyncKnowledge(AsyncAPIResource):
    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        mode: SearchMode | None = None,
        filters: SearchFilters | None = None,
        recency_weight: float | None = None,
        max_context_tokens: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> SearchResult:
        call = _search(
            query, top_k, mode, filters, recency_weight, max_context_tokens, request_options
        )
        return await self._transport.request(call, SearchResult)

    async def metrics(
        self, *, window_hours: int | None = None, request_options: RequestOptions | None = None
    ) -> KnowledgeMetrics:
        return await self._transport.request(
            _metrics(window_hours, request_options), KnowledgeMetrics
        )

    async def list_queries(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> QueryPage:
        return await self._transport.request(
            _list_queries(limit, offset, request_options), QueryPage
        )

    def iter_queries(self, *, page_size: int = 100) -> AsyncIterator[KnowledgeQuery]:
        return apaginate(
            lambda limit, offset: self.list_queries(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    async def get_query(
        self, id: str, *, request_options: RequestOptions | None = None
    ) -> KnowledgeQueryDetail:
        return await self._transport.request(_get_query(id, request_options), KnowledgeQueryDetail)
