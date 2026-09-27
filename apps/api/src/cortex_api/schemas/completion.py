import json
import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from cortex_api.models.message import MessageRole
from cortex_api.models.model_execution import RoutingMode
from cortex_api.schemas.knowledge import KnowledgeOptions, KnowledgeUsage
from cortex_api.schemas.memory import MAX_MESSAGE_CHARS
from cortex_api.services.router.base import Capability, FinishReason, ProviderName
from cortex_api.services.router.policies import Objective

MAX_MESSAGES = 500
MAX_METADATA_BYTES = 16_384


# --- Request -------------------------------------------------------------------


class ChatMessageIn(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class MemoryOptions(BaseModel):
    """Ground the completion in a Cortex conversation.

    With `include_history`, send only the new turns: prior turns come from the
    conversation's hot session.
    """

    conversation_id: uuid.UUID
    include_history: bool = True
    memory_limit: int = Field(default=5, ge=0, le=20, description="Long-term memories to inject.")
    persist: bool = Field(
        default=True, description="Append the new turns and the model output to the conversation."
    )


class RoutingOptions(BaseModel):
    mode: RoutingMode | None = Field(
        default=None,
        description="Defaults to `preferred` when a model or provider is given, else `auto`.",
    )
    provider: ProviderName | None = None
    capabilities: list[Capability] = Field(default_factory=list)
    allow_fallback: bool = True


class CompletionRequest(BaseModel):
    model: str | None = Field(
        default=None,
        max_length=128,
        description="`provider/model`, a bare model id, or an alias. Omit to let Cortex choose.",
    )
    objective: Objective | None = Field(
        default=None, description="What AUTO routing optimizes. Defaults to the org policy."
    )
    messages: list[ChatMessageIn] = Field(min_length=1, max_length=MAX_MESSAGES)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=200_000)
    memory: MemoryOptions | None = None
    knowledge: KnowledgeOptions | None = Field(
        default=None, description="Ground the answer in retrieved documents with [n] citations."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    routing: RoutingOptions = Field(default_factory=RoutingOptions)
    stream: bool = Field(
        default=False,
        description="Respond with Server-Sent Events: `start`, `token`s, then `complete`.",
    )

    @field_validator("metadata")
    @classmethod
    def _bounded_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_METADATA_BYTES:
            raise ValueError(f"metadata must serialize to at most {MAX_METADATA_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if all(m.role is MessageRole.SYSTEM for m in self.messages):
            raise ValueError("messages must include at least one non-system message")
        if self.routing.mode is RoutingMode.STRICT and not self.model:
            raise ValueError("strict routing requires `model`")
        if self.routing.mode is RoutingMode.PREFERRED and not (self.model or self.routing.provider):
            raise ValueError("preferred routing requires `model` or `routing.provider`")
        return self


# --- Response ------------------------------------------------------------------


class TokenUsage(BaseModel):
    prompt: int
    completion: int
    total: int


class AttemptRead(BaseModel):
    provider: str
    model: str
    success: bool
    latency_ms: int
    error: str | None = None


class CompletionResponse(BaseModel):
    """The only completion shape applications see, whichever provider served it."""

    id: uuid.UUID
    object: Literal["cortex.completion"] = "cortex.completion"
    created_at: datetime
    provider: str
    model: str
    output: str
    latency_ms: int
    tokens: TokenUsage
    finish_reason: FinishReason
    routing_reason: str
    routing_mode: RoutingMode
    cost_estimate: float = Field(description="USD, from catalog prices.")
    attempts: list[AttemptRead] = Field(description="Every provider call, including failures.")
    conversation_id: uuid.UUID | None = None
    knowledge: KnowledgeUsage | None = None


class CompletionErrorResponse(BaseModel):
    id: uuid.UUID
    detail: str
    attempts: list[AttemptRead]


# --- Catalog -----------------------------------------------------------------


class ModelPricing(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    currency: Literal["USD"] = "USD"


class ModelRead(BaseModel):
    id: str = Field(description="`provider/model`; accepted as `model` in completions.")
    provider: ProviderName
    model: str
    display_name: str
    capabilities: list[Capability]
    context_window: int
    max_output_tokens: int
    pricing: ModelPricing
    quality: int
    expected_latency_ms: int
    available: bool
    availability: str = Field(description="`ok`, `not_configured`, or `circuit_open`.")
    allowed: bool = Field(description="Permitted by this organization's routing policy.")


class ModelListResponse(BaseModel):
    models: list[ModelRead]


# --- Executions --------------------------------------------------------------


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    completion_id: uuid.UUID
    attempt: int
    provider: str
    model: str
    routing_mode: RoutingMode
    is_fallback: bool
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_estimate: float
    success: bool
    finish_reason: str | None
    error_type: str | None
    error: str | None
    metadata: dict[str, Any] = Field(validation_alias=AliasChoices("metadata_", "metadata"))
    created_at: datetime


class ExecutionListResponse(BaseModel):
    items: list[ExecutionRead]
    total: int
    limit: int
    offset: int
