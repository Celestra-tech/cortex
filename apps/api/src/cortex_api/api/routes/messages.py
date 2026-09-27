from fastapi import APIRouter, status

from cortex_api.api.deps import MemoryServiceDep, OrganizationDep
from cortex_api.schemas.memory import MessageCreate, MessageRead

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
async def create_message(
    body: MessageCreate, organization: OrganizationDep, service: MemoryServiceDep
) -> MessageRead:
    message = await service.append_message(
        organization.id,
        body.conversation_id,
        role=body.role,
        content=body.content,
        metadata=body.metadata,
    )
    return MessageRead.model_validate(message)
