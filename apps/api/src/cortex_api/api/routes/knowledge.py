import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from cortex_api.api.deps import KnowledgeServiceDep, OrganizationDep
from cortex_api.schemas.knowledge import (
    KnowledgeMetricsResponse,
    KnowledgeQueryDetail,
    KnowledgeQueryListResponse,
    KnowledgeQueryRead,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search(
    body: KnowledgeSearchRequest, organization: OrganizationDep, knowledge: KnowledgeServiceDep
) -> KnowledgeSearchResponse:
    """Hybrid retrieval plus an assembled, citation-numbered context window."""
    retrieval = await knowledge.retrieve(
        organization.id,
        body.query,
        top_k=body.top_k,
        mode=body.mode,
        filters=body.filters.to_filters() if body.filters else None,
        recency_weight=body.recency_weight,
        max_context_tokens=body.max_context_tokens,
    )
    return KnowledgeSearchResponse.build(retrieval.query_id, retrieval.context)


@router.get("/queries", response_model=KnowledgeQueryListResponse)
async def list_queries(
    organization: OrganizationDep,
    knowledge: KnowledgeServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgeQueryListResponse:
    """Logged retrievals, newest first."""
    items, total = await knowledge.list_queries(organization.id, limit=limit, offset=offset)
    return KnowledgeQueryListResponse(
        items=[KnowledgeQueryRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/queries/{query_id}", response_model=KnowledgeQueryDetail)
async def get_query(
    query_id: uuid.UUID, organization: OrganizationDep, knowledge: KnowledgeServiceDep
) -> KnowledgeQueryDetail:
    """A retrieval with its ranked results, for replaying citations."""
    return KnowledgeQueryDetail.model_validate(await knowledge.get_query(organization.id, query_id))


@router.get("/metrics", response_model=KnowledgeMetricsResponse)
async def metrics(
    organization: OrganizationDep,
    knowledge: KnowledgeServiceDep,
    window_hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
) -> KnowledgeMetricsResponse:
    """Corpus size, ingestion and retrieval latency, retrieval quality, and citation usage."""
    values = await knowledge.metrics(organization.id, timedelta(hours=window_hours))
    return KnowledgeMetricsResponse.model_validate({"window_hours": window_hours, **values})
