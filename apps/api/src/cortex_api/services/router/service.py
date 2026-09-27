import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.message import MessageRole
from cortex_api.models.model_execution import ModelExecution
from cortex_api.models.organization import Organization
from cortex_api.repositories.execution_repository import ExecutionRepository
from cortex_api.schemas.completion import (
    AttemptRead,
    CompletionRequest,
    CompletionResponse,
    MemoryOptions,
    TokenUsage,
)
from cortex_api.schemas.knowledge import KnowledgeUsage, context_read
from cortex_api.services.knowledge.assembler import AssembledContext
from cortex_api.services.knowledge.citations import CITATION_INSTRUCTIONS, cited_indices
from cortex_api.services.knowledge.service import KnowledgeService, Retrieval
from cortex_api.services.memory.service import MemoryService
from cortex_api.services.observatory.events import EventPublisher, EventType
from cortex_api.services.router.base import ChatMessage
from cortex_api.services.router.fallback import Attempt
from cortex_api.services.router.policies import OrganizationPolicy, RoutingError, RoutingPlan
from cortex_api.services.router.router import (
    CompletionFailedError,
    CompletionTask,
    CortexRouter,
    RoutedCompletion,
)

MEMORY_PREAMBLE = (
    "Relevant long-term memory for this organization, most relevant first. "
    "Use it when it helps; do not mention it otherwise."
)


class CompletionService:
    """Routes one completion and records every provider attempt.

    Execution rows are committed before success or failure is returned, so
    the observability log is complete even when every provider fails.
    """

    def __init__(
        self,
        session: AsyncSession,
        router: CortexRouter,
        memory: MemoryService,
        knowledge: KnowledgeService | None = None,
        events: EventPublisher | None = None,
    ) -> None:
        self.session = session
        self.router = router
        self.memory = memory
        self.knowledge = knowledge
        self.events = events or EventPublisher(None)
        self.executions = ExecutionRepository(session)

    async def complete(
        self, organization: Organization, request: CompletionRequest
    ) -> CompletionResponse:
        request_id = uuid7()
        await self.events.publish(
            organization.id,
            EventType.REQUEST_RECEIVED,
            {
                "request_id": request_id,
                "model": request.model,
                "provider": request.routing.provider,
                "routing_mode": request.routing.mode,
                "objective": request.objective,
                "messages": len(request.messages),
                "memory": request.memory is not None,
                "knowledge": request.knowledge is not None,
            },
        )
        try:
            return await self._complete(organization, request, request_id)
        except CompletionFailedError as exc:
            await self.events.publish(
                organization.id,
                EventType.EXECUTION_FAILED,
                {
                    "request_id": request_id,
                    "completion_id": exc.completion_id,
                    "error": str(exc),
                    "attempts": len(exc.attempts),
                },
            )
            raise
        except RoutingError as exc:
            await self.events.publish(
                organization.id,
                EventType.EXECUTION_FAILED,
                {"request_id": request_id, "completion_id": None, "error": exc.message},
            )
            raise
        except Exception as exc:
            # Resolves the live `request.received` row; details stay in the server log.
            await self.events.publish(
                organization.id,
                EventType.EXECUTION_FAILED,
                {"request_id": request_id, "completion_id": None, "error": type(exc).__name__},
            )
            raise

    async def _complete(
        self, organization: Organization, request: CompletionRequest, request_id: uuid.UUID
    ) -> CompletionResponse:
        policy = OrganizationPolicy.from_organization_settings(organization.settings)
        messages = [ChatMessage(m.role, m.content) for m in request.messages]
        memory_usage: dict[str, Any] | None = None
        if request.memory is not None:
            messages, memory_usage = await self._with_memory(
                organization.id, request.memory, messages
            )
        grounding = None
        if request.knowledge is not None:
            grounding = await self._retrieve(organization.id, request)
            if grounding.applied:
                messages = _with_sources(messages, grounding.retrieval.context)

        task = CompletionTask(
            messages=tuple(messages),
            policy=policy,
            model=request.model,
            provider=request.routing.provider,
            mode=request.routing.mode,
            objective=request.objective,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            capabilities=frozenset(request.routing.capabilities),
            allow_fallback=request.routing.allow_fallback,
        )
        extra: dict[str, Any] = {
            "request_id": str(request_id),
            "prompt": {
                "messages": len(messages),
                "system_messages": sum(m.role is MessageRole.SYSTEM for m in messages),
                "estimated_tokens": self.router.estimate_prompt_tokens(messages),
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            },
            "memory": memory_usage,
        }
        if grounding:
            extra["knowledge_query_id"] = str(grounding.retrieval.query_id)
        try:
            routed = await self.router.complete(task)
        except CompletionFailedError as exc:
            await self._record(
                organization.id,
                exc.completion_id,
                exc.plan,
                exc.attempts,
                request.metadata,
                extra=extra,
            )
            await self.session.commit()
            raise

        knowledge_usage = None
        if grounding is not None and self.knowledge is not None:
            context = grounding.retrieval.context
            cited = (
                cited_indices(routed.response.output, len(context.citations))
                if grounding.applied
                else []
            )
            await self.knowledge.record_citations(
                grounding.retrieval.query_id, completion_id=routed.id, cited=cited
            )
            read = context_read(context, set(cited) if grounding.applied else None)
            knowledge_usage = KnowledgeUsage(
                query_id=grounding.retrieval.query_id,
                applied=grounding.applied,
                confidence=read.confidence,
                citations=read.citations,
                cited=cited,
            )

        await self._record(
            organization.id,
            routed.id,
            routed.plan,
            routed.attempts,
            request.metadata,
            routed,
            extra=extra,
        )
        await self.session.commit()
        await self.events.publish(
            organization.id,
            EventType.EXECUTION_COMPLETED,
            {
                "request_id": request_id,
                "completion_id": routed.id,
                "provider": str(routed.spec.provider),
                "model": routed.spec.id,
                "objective": str(routed.plan.objective),
                "routing_mode": str(routed.plan.mode),
                "routing_reason": routed.routing_reason,
                "latency_ms": routed.latency_ms,
                "prompt_tokens": routed.prompt_tokens,
                "completion_tokens": routed.completion_tokens,
                "cost_estimate": float(routed.cost_estimate),
                "attempts": len(routed.attempts),
                "fallback": any(a.is_fallback for a in routed.attempts),
                "grounded": bool(grounding and grounding.applied),
            },
        )

        if request.memory is not None and request.memory.persist:
            await self._persist(organization.id, request.memory.conversation_id, request, routed)

        return CompletionResponse(
            id=routed.id,
            created_at=routed.created_at,
            provider=str(routed.spec.provider),
            model=routed.spec.id,
            output=routed.response.output,
            latency_ms=routed.latency_ms,
            tokens=TokenUsage(
                prompt=routed.prompt_tokens,
                completion=routed.completion_tokens,
                total=routed.prompt_tokens + routed.completion_tokens,
            ),
            finish_reason=routed.response.finish_reason,
            routing_reason=routed.routing_reason,
            routing_mode=routed.plan.mode,
            cost_estimate=float(routed.cost_estimate),
            attempts=attempt_summaries(routed.attempts),
            conversation_id=request.memory.conversation_id if request.memory else None,
            knowledge=knowledge_usage,
        )

    async def _retrieve(
        self, organization_id: uuid.UUID, request: CompletionRequest
    ) -> "_Grounding":
        options = request.knowledge
        if options is None or self.knowledge is None:
            raise RuntimeError("knowledge grounding requested but no KnowledgeService is wired")
        query = options.query or _grounding_query(request)
        retrieval = await self.knowledge.retrieve(
            organization_id,
            query,
            top_k=options.top_k,
            mode=options.mode,
            filters=options.filters.to_filters() if options.filters else None,
            max_context_tokens=options.max_context_tokens,
            commit=False,
        )
        context = retrieval.context
        applied = bool(context.citations) and context.confidence.score >= options.min_confidence
        return _Grounding(retrieval, applied)

    async def _with_memory(
        self, organization_id: uuid.UUID, options: MemoryOptions, messages: list[ChatMessage]
    ) -> tuple[list[ChatMessage], dict[str, Any]]:
        """The grounded message list, plus what memory contributed (for the execution log)."""
        # Also validates that the conversation belongs to the organization.
        context = await self.memory.get_recent_context(organization_id, options.conversation_id)
        history = (
            [ChatMessage(m.role, m.content) for m in context.messages]
            if options.include_history
            else []
        )

        recalled: list[ChatMessage] = []
        memory_ids: list[str] = []
        query = next((m.content for m in reversed(messages) if m.role is MessageRole.USER), None)
        if options.memory_limit and query:
            ranked = await self.memory.retrieve_memories(
                organization_id, query=query, limit=options.memory_limit
            )
            if ranked:
                lines = "\n".join(f"- [{r.memory.type}] {r.memory.summary}" for r in ranked)
                recalled = [ChatMessage(MessageRole.SYSTEM, f"{MEMORY_PREAMBLE}\n{lines}")]
                memory_ids = [str(r.memory.id) for r in ranked]

        system = [m for m in messages if m.role is MessageRole.SYSTEM]
        turns = [m for m in messages if m.role is not MessageRole.SYSTEM]
        usage = {
            "conversation_id": str(options.conversation_id),
            "source": context.source,
            "history_messages": len(history),
            "history_tokens": sum(m.token_count for m in context.messages) if history else 0,
            "memories_recalled": len(memory_ids),
            "memory_ids": memory_ids,
            "persisted": options.persist,
        }
        return [*system, *recalled, *history, *turns], usage

    async def _persist(
        self,
        organization_id: uuid.UUID,
        conversation_id: uuid.UUID,
        request: CompletionRequest,
        routed: RoutedCompletion,
    ) -> None:
        link = {"completion_id": str(routed.id)}
        for message in request.messages:
            if message.role is not MessageRole.SYSTEM:
                await self.memory.append_message(
                    organization_id,
                    conversation_id,
                    role=message.role,
                    content=message.content,
                    metadata=link,
                )
        if routed.response.output:
            await self.memory.append_message(
                organization_id,
                conversation_id,
                role=MessageRole.ASSISTANT,
                content=routed.response.output,
                metadata={**link, "provider": str(routed.spec.provider), "model": routed.spec.id},
            )

    async def _record(
        self,
        organization_id: uuid.UUID,
        completion_id: uuid.UUID,
        plan: RoutingPlan,
        attempts: Sequence[Attempt],
        request_metadata: dict[str, Any],
        routed: RoutedCompletion | None = None,
        *,
        extra: dict[str, Any] | None = None,
    ) -> list[ModelExecution]:
        rows = []
        for attempt in attempts:
            won = routed is not None and attempt.success
            response = attempt.response
            rows.append(
                ModelExecution(
                    organization_id=organization_id,
                    completion_id=completion_id,
                    attempt=attempt.number,
                    provider=str(attempt.spec.provider),
                    model=attempt.spec.id,
                    routing_mode=plan.mode,
                    is_fallback=attempt.is_fallback,
                    latency_ms=attempt.latency_ms,
                    prompt_tokens=routed.prompt_tokens if won and routed else 0,
                    completion_tokens=routed.completion_tokens if won and routed else 0,
                    cost_estimate=routed.cost_estimate if won and routed else 0,
                    success=attempt.success,
                    finish_reason=str(response.finish_reason) if response else None,
                    error_type=str(attempt.error.kind) if attempt.error else None,
                    error=attempt.error.describe() if attempt.error else None,
                    metadata_={
                        "objective": str(plan.objective),
                        "routing_reason": routed.routing_reason if routed else plan.reason,
                        "provider_model": response.provider_model if response else None,
                        "provider_request_id": response.provider_request_id if response else None,
                        "request": request_metadata,
                        **(extra or {}),
                    },
                )
            )
        return await self.executions.record_many(rows)


@dataclass(frozen=True, slots=True)
class _Grounding:
    retrieval: Retrieval
    applied: bool


def _grounding_query(request: CompletionRequest) -> str:
    """The last user turn, else the last non-system turn."""
    turns = [m for m in request.messages if m.role is not MessageRole.SYSTEM]
    users = [m for m in turns if m.role is MessageRole.USER]
    return (users or turns)[-1].content


def _with_sources(messages: list[ChatMessage], context: AssembledContext) -> list[ChatMessage]:
    """Adds the numbered sources after the leading system messages."""
    sources = ChatMessage(MessageRole.SYSTEM, f"{CITATION_INSTRUCTIONS}\n\n{context.text}")
    split = next(
        (i for i, m in enumerate(messages) if m.role is not MessageRole.SYSTEM), len(messages)
    )
    return [*messages[:split], sources, *messages[split:]]


def attempt_summaries(attempts: Sequence[Attempt]) -> list[AttemptRead]:
    return [
        AttemptRead(
            provider=str(a.spec.provider),
            model=a.spec.id,
            success=a.success,
            latency_ms=a.latency_ms,
            error=a.error.describe() if a.error else None,
        )
        for a in attempts
    ]
