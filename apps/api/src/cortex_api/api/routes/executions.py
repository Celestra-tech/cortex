import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from cortex_api.api.deps import OrganizationDep
from cortex_api.database.session import DbSession
from cortex_api.models.model_execution import ModelExecution
from cortex_api.repositories.base import NotFoundError
from cortex_api.repositories.execution_repository import ExecutionFilters, ExecutionRepository
from cortex_api.schemas.completion import ExecutionListResponse, ExecutionRead

router = APIRouter(prefix="/executions", tags=["executions"])


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    organization: OrganizationDep,
    session: DbSession,
    provider: Annotated[str | None, Query(max_length=32)] = None,
    model: Annotated[str | None, Query(max_length=128)] = None,
    success: bool | None = None,
    completion_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=ExecutionRepository.max_limit)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExecutionListResponse:
    """Newest first."""
    repository = ExecutionRepository(session)
    filters = ExecutionFilters(
        provider=provider, model=model, success=success, completion_id=completion_id
    )
    items = await repository.list_for_organization(
        organization.id, filters, limit=limit, offset=offset
    )
    total = await repository.count_for_organization(organization.id, filters)
    return ExecutionListResponse(
        items=[ExecutionRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}", response_model=ExecutionRead)
async def get_execution(
    execution_id: uuid.UUID, organization: OrganizationDep, session: DbSession
) -> ExecutionRead:
    execution = await ExecutionRepository(session).get_for_organization(
        organization.id, execution_id
    )
    if execution is None:
        raise NotFoundError(ModelExecution, execution_id)
    return ExecutionRead.model_validate(execution)
