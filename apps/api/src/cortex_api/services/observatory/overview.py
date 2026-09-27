import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.repositories.observatory_repository import (
    BUCKET_ORIGIN,
    BucketAggregate,
    ObservatoryRepository,
    RequestAggregate,
)


def bucket_width(window: timedelta) -> timedelta:
    """Keeps the timeline between roughly 24 and 96 points."""
    if window <= timedelta(hours=6):
        return timedelta(minutes=15)
    if window <= timedelta(hours=48):
        return timedelta(hours=1)
    if window <= timedelta(days=7):
        return timedelta(hours=6)
    return timedelta(days=1)


def floor_to_bucket(moment: datetime, width: timedelta) -> datetime:
    return BUCKET_ORIGIN + ((moment - BUCKET_ORIGIN) // width) * width


def fill_buckets(
    buckets: list[BucketAggregate], start: datetime, end: datetime, width: timedelta
) -> list[dict[str, Any]]:
    """Every bucket overlapping [start, end), with empty ones as zero requests."""
    by_start = {bucket.start: bucket for bucket in buckets}
    filled = []
    cursor = floor_to_bucket(start, width)
    while cursor < end:
        bucket = by_start.get(cursor)
        filled.append(
            asdict(bucket)
            if bucket is not None
            else {
                "start": cursor,
                "requests": 0,
                "failed": 0,
                "avg_latency_ms": None,
                "p95_latency_ms": None,
            }
        )
        cursor += width
    return filled


def request_stats(aggregate: RequestAggregate) -> dict[str, Any]:
    total = aggregate.total
    return {
        **asdict(aggregate),
        "success_rate": (total - aggregate.failed) / total if total else None,
        "fallback_rate": aggregate.fallbacks / total if total else None,
    }


class OverviewService:
    """The Observatory's landing numbers for one organization and time window."""

    def __init__(self, session: AsyncSession) -> None:
        self.repository = ObservatoryRepository(session)

    async def overview(
        self, organization_id: uuid.UUID, window_hours: int, *, now: datetime | None = None
    ) -> dict[str, Any]:
        end = now or datetime.now(UTC)
        window = timedelta(hours=window_hours)
        start = end - window
        width = bucket_width(window)

        current = await self.repository.request_aggregates(organization_id, start, end)
        previous = await self.repository.request_aggregates(organization_id, start - window, start)
        buckets = await self.repository.request_buckets(
            organization_id, floor_to_bucket(start, width), end, width
        )
        providers = await self.repository.provider_distribution(organization_id, start, end)
        activity = await self.repository.activity_counts(organization_id, start, end)

        served = sum(provider.requests for provider in providers)
        return {
            "window_hours": window_hours,
            "generated_at": end,
            "bucket_seconds": int(width.total_seconds()),
            "requests": request_stats(current),
            "previous": request_stats(previous),
            "timeline": fill_buckets(buckets, start, end, width),
            "providers": [
                {**asdict(provider), "share": provider.requests / served} for provider in providers
            ],
            **activity,
        }
