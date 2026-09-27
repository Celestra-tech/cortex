import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from cortex_api.models.memory import MemoryType
from cortex_api.models.message import MessageRole

MAX_MESSAGE_CHARS = 100_000
MAX_MEMORY_CHARS = 20_000
MAX_SUMMARY_CHARS = 1_000


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Conversations ---------------------------------------------------------


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ConversationRead(_ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationSummaryRead(ConversationRead):
    message_count: int
    session: Literal["hot", "cold"] | None = Field(
        description="Whether a Redis session is cached; null when Redis was unreachable."
    )


class ConversationListResponse(BaseModel):
    items: list[ConversationSummaryRead]
    total: int
    limit: int
    offset: int


class ConversationDetail(ConversationRead):
    message_count: int
    messages: list["MessageRead"] = Field(description="Most recent messages, oldest first.")


# --- Messages ----------------------------------------------------------------


class MessageCreate(BaseModel):
    conversation_id: uuid.UUID
    role: MessageRole
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRead(_ORMModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: MessageRole
    content: str
    token_count: int
    metadata: dict[str, Any] = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    created_at: datetime


# --- Hot session (Redis) ---------------------------------------------------


class SessionMessage(BaseModel):
    """Compact message form held in the Redis session. Metadata is intentionally omitted."""

    id: uuid.UUID
    role: MessageRole
    content: str
    token_count: int
    created_at: datetime


class SessionState(BaseModel):
    conversation_id: uuid.UUID
    messages: list[SessionMessage]
    token_count: int
    last_activity: datetime


# --- Persistent memories ---------------------------------------------------


class MemoryCreate(BaseModel):
    type: MemoryType
    content: str = Field(min_length=1, max_length=MAX_MEMORY_CHARS)
    summary: str | None = Field(
        default=None,
        max_length=MAX_SUMMARY_CHARS,
        description="Derived from content when omitted.",
    )
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_message_id: uuid.UUID | None = None


class MemoryRead(_ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    type: MemoryType
    summary: str
    content: str
    importance: float
    source_message_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class RankedMemoryRead(MemoryRead):
    score: float = Field(
        description="Relevance * importance * recency. Comparable within one response only."
    )

    @classmethod
    def from_ranked(cls, memory: object, score: float) -> "RankedMemoryRead":
        fields = MemoryRead.model_validate(memory).model_dump()
        return cls.model_validate({**fields, "score": score})


class MemorySearchResponse(BaseModel):
    query: str | None
    memories: list[RankedMemoryRead]


# --- Context -------------------------------------------------------------


class ContextRead(BaseModel):
    """Everything a model needs for the next turn: hot session plus relevant long-term memory."""

    conversation_id: uuid.UUID
    messages: list[SessionMessage]
    token_count: int
    last_activity: datetime
    source: Literal["cache", "database"]
    memories: list[RankedMemoryRead]
