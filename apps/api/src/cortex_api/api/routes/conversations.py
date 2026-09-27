import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from cortex_api.api.deps import MemoryServiceDep, OrganizationDep
from cortex_api.models.message import MessageRole
from cortex_api.schemas.memory import (
    ContextRead,
    ConversationCreate,
    ConversationDetail,
    ConversationListResponse,
    ConversationRead,
    ConversationSummaryRead,
    MessageRead,
    RankedMemoryRead,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, organization: OrganizationDep, service: MemoryServiceDep
) -> ConversationRead:
    conversation = await service.create_conversation(organization.id, title=body.title)
    return ConversationRead.model_validate(conversation)


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    organization: OrganizationDep,
    service: MemoryServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationListResponse:
    """Most recently active first."""
    summaries, total = await service.list_conversations(organization.id, limit=limit, offset=offset)
    return ConversationListResponse(
        items=[
            ConversationSummaryRead(
                **ConversationRead.model_validate(summary.conversation).model_dump(),
                message_count=summary.message_count,
                session=None if summary.hot is None else "hot" if summary.hot else "cold",
            )
            for summary in summaries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    organization: OrganizationDep,
    service: MemoryServiceDep,
    message_limit: Annotated[int, Query(ge=0, le=500)] = 100,
) -> ConversationDetail:
    conversation = await service.get_conversation(organization.id, conversation_id)
    messages, total = await service.list_messages(
        organization.id, conversation_id, limit=message_limit
    )
    return ConversationDetail(
        **ConversationRead.model_validate(conversation).model_dump(),
        message_count=total,
        messages=[MessageRead.model_validate(message) for message in messages],
    )


@router.get("/{conversation_id}/context", response_model=ContextRead)
async def get_conversation_context(
    conversation_id: uuid.UUID,
    organization: OrganizationDep,
    service: MemoryServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=1000, description="Max recent messages.")] = None,
    query: Annotated[
        str | None,
        Query(max_length=1000, description="Memory search text; defaults to last user message."),
    ] = None,
    memory_limit: Annotated[int, Query(ge=0, le=50)] = 5,
) -> ContextRead:
    context = await service.get_recent_context(organization.id, conversation_id, limit=limit)

    memory_query = query
    if memory_query is None:
        memory_query = next(
            (m.content for m in reversed(context.messages) if m.role is MessageRole.USER), None
        )

    memories = (
        await service.retrieve_memories(organization.id, query=memory_query, limit=memory_limit)
        if memory_limit
        else []
    )
    return ContextRead(
        conversation_id=context.conversation_id,
        messages=context.messages,
        token_count=context.token_count,
        last_activity=context.last_activity,
        source=context.source,
        memories=[RankedMemoryRead.from_ranked(ranked.memory, ranked.score) for ranked in memories],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, organization: OrganizationDep, service: MemoryServiceDep
) -> Response:
    await service.delete_conversation(organization.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
