from typing import Annotated

from fastapi import APIRouter, Query, status

from cortex_api.api.deps import MemoryServiceDep, OrganizationDep
from cortex_api.models.memory import MemoryType
from cortex_api.schemas.memory import (
    MemoryCreate,
    MemoryRead,
    MemorySearchResponse,
    RankedMemoryRead,
)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.post("", response_model=MemoryRead, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate, organization: OrganizationDep, service: MemoryServiceDep
) -> MemoryRead:
    memory = await service.store_memory(
        organization.id,
        type=body.type,
        content=body.content,
        summary=body.summary,
        importance=body.importance,
        source_message_id=body.source_message_id,
    )
    return MemoryRead.model_validate(memory)


@router.get("", response_model=MemorySearchResponse)
async def search_memories(
    organization: OrganizationDep,
    service: MemoryServiceDep,
    query: Annotated[str | None, Query(max_length=1000)] = None,
    type: Annotated[list[MemoryType] | None, Query()] = None,
    min_importance: Annotated[float, Query(ge=0.0, le=1.0)] = 0.0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> MemorySearchResponse:
    ranked = await service.retrieve_memories(
        organization.id, query=query, types=type, min_importance=min_importance, limit=limit
    )
    return MemorySearchResponse(
        query=query,
        memories=[RankedMemoryRead.from_ranked(item.memory, item.score) for item in ranked],
    )
