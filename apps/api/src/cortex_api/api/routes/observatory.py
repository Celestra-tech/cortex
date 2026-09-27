from typing import Annotated

from fastapi import APIRouter, Query

from cortex_api.api.deps import OrganizationDep
from cortex_api.database.session import DbSession
from cortex_api.schemas.observatory import OverviewResponse
from cortex_api.schemas.organization import OrganizationRead
from cortex_api.services.observatory.overview import OverviewService

router = APIRouter(tags=["observatory"])


@router.get("/organization", response_model=OrganizationRead)
async def get_organization(organization: OrganizationDep) -> OrganizationRead:
    """The organization named by `X-Organization-ID`."""
    return OrganizationRead.model_validate(organization)


@router.get("/observatory/overview", response_model=OverviewResponse)
async def get_overview(
    organization: OrganizationDep,
    session: DbSession,
    window_hours: Annotated[int, Query(ge=1, le=24 * 90)] = 24,
) -> OverviewResponse:
    """Request volume, latency, provider mix and corpus size for the window."""
    data = await OverviewService(session).overview(organization.id, window_hours)
    return OverviewResponse.model_validate(data)
