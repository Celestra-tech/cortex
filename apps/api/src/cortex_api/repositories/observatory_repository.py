import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, Subquery, and_, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.conversation import Conversation
from cortex_api.models.document import Document
from cortex_api.models.knowledge_query import KnowledgeQuery
from cortex_api.models.memory import Memory
from cortex_api.models.model_execution import ModelExecution

# Fixed origin so buckets line up across requests regardless of `now`.
BUCKET_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RequestAggregate:
    total: int
    failed: int
    avg_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    tokens: int
    cost_estimate: float
    fallbacks: int


@dataclass(frozen=True, slots=True)
class BucketAggregate:
    start: datetime
    requests: int
    failed: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class ProviderAggregate:
    provider: str
    requests: int
    avg_latency_ms: float
    tokens: int
    cost_estimate: float
    models: list[str]


class ObservatoryRepository:
    """Read-only aggregates across the router, memory and knowledge tables.

    A request is one completion: all attempts sharing `completion_id`. Its
    latency is the sum of attempt latencies (what the caller waited for) and it
    counts as failed only when no attempt succeeded.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _completions(self, organization_id: uuid.UUID, since: datetime) -> Subquery:
        return (
            select(
                ModelExecution.completion_id,
                func.min(ModelExecution.created_at).label("started_at"),
                func.sum(ModelExecution.latency_ms).label("latency_ms"),
                func.bool_or(ModelExecution.success).label("success"),
                func.bool_or(ModelExecution.is_fallback).label("fallback"),
                func.sum(ModelExecution.prompt_tokens + ModelExecution.completion_tokens).label(
                    "tokens"
                ),
                func.sum(ModelExecution.cost_estimate).label("cost"),
            )
            .where(
                ModelExecution.organization_id == organization_id,
                ModelExecution.created_at >= since,
            )
            .group_by(ModelExecution.completion_id)
            .subquery("completions")
        )

    async def request_aggregates(
        self, organization_id: uuid.UUID, start: datetime, end: datetime
    ) -> RequestAggregate:
        completions = self._completions(organization_id, start)
        latency = completions.c.latency_ms
        statement = select(
            func.count(),
            func.count().filter(~completions.c.success),
            func.avg(latency),
            func.percentile_cont(0.5).within_group(latency),
            func.percentile_cont(0.95).within_group(latency),
            func.coalesce(func.sum(completions.c.tokens), 0),
            func.coalesce(func.sum(completions.c.cost), 0),
            func.count().filter(completions.c.fallback),
        ).where(completions.c.started_at >= start, completions.c.started_at < end)
        row = (await self.session.execute(statement)).one()
        return RequestAggregate(
            total=row[0],
            failed=row[1],
            avg_latency_ms=_float(row[2]),
            p50_latency_ms=_float(row[3]),
            p95_latency_ms=_float(row[4]),
            tokens=int(row[5]),
            cost_estimate=float(row[6]),
            fallbacks=row[7],
        )

    async def request_buckets(
        self, organization_id: uuid.UUID, start: datetime, end: datetime, width: timedelta
    ) -> list[BucketAggregate]:
        """Only non-empty buckets; the caller zero-fills."""
        completions = self._completions(organization_id, start)
        bucket = func.date_bin(literal(width), completions.c.started_at, literal(BUCKET_ORIGIN))
        latency = completions.c.latency_ms
        statement = (
            select(
                bucket.label("bucket"),
                func.count(),
                func.count().filter(~completions.c.success),
                func.avg(latency),
                func.percentile_cont(0.95).within_group(latency),
            )
            .where(completions.c.started_at >= start, completions.c.started_at < end)
            .group_by(bucket)
            .order_by(bucket)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            BucketAggregate(
                start=row[0],
                requests=row[1],
                failed=row[2],
                avg_latency_ms=_float(row[3]),
                p95_latency_ms=_float(row[4]),
            )
            for row in rows
        ]

    async def provider_distribution(
        self, organization_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[ProviderAggregate]:
        """Who actually answered: successful attempts only, busiest first."""
        statement = (
            select(
                ModelExecution.provider,
                func.count(),
                func.avg(ModelExecution.latency_ms),
                func.sum(ModelExecution.prompt_tokens + ModelExecution.completion_tokens),
                func.sum(ModelExecution.cost_estimate),
                func.array_agg(func.distinct(ModelExecution.model)),
            )
            .where(
                ModelExecution.organization_id == organization_id,
                ModelExecution.success.is_(True),
                ModelExecution.created_at >= start,
                ModelExecution.created_at < end,
            )
            .group_by(ModelExecution.provider)
            .order_by(func.count().desc(), ModelExecution.provider)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            ProviderAggregate(
                provider=row[0],
                requests=row[1],
                avg_latency_ms=float(row[2]),
                tokens=int(row[3]),
                cost_estimate=float(row[4]),
                models=sorted(row[5]),
            )
            for row in rows
        ]

    async def activity_counts(
        self, organization_id: uuid.UUID, start: datetime, end: datetime
    ) -> dict[str, Any]:
        in_window = and_(Conversation.updated_at >= start, Conversation.updated_at < end)
        conversations = select(
            func.count().filter(in_window),
            func.count(),
        ).where(Conversation.organization_id == organization_id, Conversation.deleted_at.is_(None))
        documents = select(
            func.count(),
            func.coalesce(func.sum(Document.chunk_count), 0),
            func.count().filter(Document.created_at >= start, Document.created_at < end),
            func.coalesce(func.sum(Document.token_count), 0),
        ).where(Document.organization_id == organization_id)
        memories = select(func.count()).where(
            Memory.organization_id == organization_id, Memory.deleted_at.is_(None)
        )
        queries = select(
            func.count(),
            func.avg(KnowledgeQuery.confidence),
            func.avg(cast(case((KnowledgeQuery.result_count == 0, 1.0), else_=0.0), Float)),
        ).where(
            KnowledgeQuery.organization_id == organization_id,
            KnowledgeQuery.created_at >= start,
            KnowledgeQuery.created_at < end,
        )
        conv = (await self.session.execute(conversations)).one()
        docs = (await self.session.execute(documents)).one()
        mem = (await self.session.execute(memories)).scalar_one()
        qry = (await self.session.execute(queries)).one()
        return {
            "conversations": {"active": conv[0], "total": conv[1]},
            "knowledge": {
                "documents": docs[0],
                "chunks": int(docs[1]),
                "indexed_in_window": docs[2],
                "tokens": int(docs[3]),
                "queries_in_window": qry[0],
                "avg_confidence": _float(qry[1]),
                "zero_result_rate": _float(qry[2]),
            },
            "memories": {"total": mem},
        }


def _float(value: Any) -> float | None:
    return None if value is None else round(float(value), 3)
