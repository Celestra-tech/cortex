import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.model_execution import ModelExecution, RoutingMode
from cortex_api.models.organization import Organization
from cortex_api.repositories.observatory_repository import BucketAggregate
from cortex_api.services.observatory.overview import (
    OverviewService,
    bucket_width,
    fill_buckets,
    floor_to_bucket,
)

from ..conftest import OrganizationFactory

NOW = datetime(2026, 9, 27, 14, 40, tzinfo=UTC)


class TestBuckets:
    @pytest.mark.parametrize(
        ("hours", "width"),
        [
            (1, timedelta(minutes=15)),
            (6, timedelta(minutes=15)),
            (24, timedelta(hours=1)),
            (48, timedelta(hours=1)),
            (24 * 7, timedelta(hours=6)),
            (24 * 30, timedelta(days=1)),
        ],
    )
    def test_width_scales_with_the_window(self, hours: int, width: timedelta) -> None:
        assert bucket_width(timedelta(hours=hours)) == width

    def test_floor_aligns_to_the_fixed_origin(self) -> None:
        assert floor_to_bucket(NOW, timedelta(hours=1)) == datetime(2026, 9, 27, 14, tzinfo=UTC)
        assert floor_to_bucket(NOW, timedelta(minutes=15)) == datetime(
            2026, 9, 27, 14, 30, tzinfo=UTC
        )

    def test_gaps_are_zero_filled(self) -> None:
        width = timedelta(hours=1)
        hit = BucketAggregate(datetime(2026, 9, 27, 12, tzinfo=UTC), 4, 1, 120.0, 300.0)
        filled = fill_buckets([hit], NOW - timedelta(hours=3), NOW, width)
        assert [b["start"].hour for b in filled] == [11, 12, 13, 14]
        assert [b["requests"] for b in filled] == [0, 4, 0, 0]
        assert filled[0]["avg_latency_ms"] is None


def attempt(
    organization: Organization,
    completion_id: uuid.UUID,
    *,
    at: datetime,
    provider: str = "anthropic",
    success: bool = True,
    latency_ms: int = 100,
    attempt: int = 1,
    fallback: bool = False,
) -> ModelExecution:
    return ModelExecution(
        organization_id=organization.id,
        completion_id=completion_id,
        attempt=attempt,
        provider=provider,
        model=f"{provider}-model",
        routing_mode=RoutingMode.AUTO,
        is_fallback=fallback,
        latency_ms=latency_ms,
        prompt_tokens=10,
        completion_tokens=5,
        cost_estimate=Decimal("0.001"),
        success=success,
        error=None if success else "boom",
        created_at=at,
    )


@pytest.mark.database
class TestOverviewAggregates:
    @pytest.fixture
    async def seeded(self, session: AsyncSession, organization: Organization) -> None:
        recent = NOW - timedelta(minutes=90)
        first, fallback, failed, old = (uuid.uuid4() for _ in range(4))
        session.add_all(
            [
                attempt(organization, first, at=recent, latency_ms=100),
                attempt(
                    organization,
                    fallback,
                    at=recent,
                    provider="openai",
                    success=False,
                    latency_ms=50,
                ),
                attempt(
                    organization, fallback, at=recent, latency_ms=150, attempt=2, fallback=True
                ),
                attempt(
                    organization,
                    failed,
                    at=NOW - timedelta(minutes=10),
                    success=False,
                    latency_ms=30,
                ),
                attempt(organization, old, at=NOW - timedelta(hours=30), provider="gemini"),
            ]
        )
        await session.flush()

    async def test_requests_are_completions_not_attempts(
        self, session: AsyncSession, organization: Organization, seeded: None
    ) -> None:
        data = await OverviewService(session).overview(organization.id, 24, now=NOW)
        requests = data["requests"]
        assert (requests["total"], requests["failed"], requests["fallbacks"]) == (3, 1, 1)
        assert requests["success_rate"] == pytest.approx(2 / 3)
        assert requests["fallback_rate"] == pytest.approx(1 / 3)
        assert requests["avg_latency_ms"] == pytest.approx(110.0)
        assert requests["p50_latency_ms"] == pytest.approx(100.0)
        assert requests["tokens"] == 4 * 15
        assert requests["cost_estimate"] == pytest.approx(0.004)
        assert data["previous"]["total"] == 1

    async def test_timeline_covers_the_window_and_sums_to_the_total(
        self, session: AsyncSession, organization: Organization, seeded: None
    ) -> None:
        data = await OverviewService(session).overview(organization.id, 24, now=NOW)
        timeline = data["timeline"]
        assert data["bucket_seconds"] == 3600
        assert len(timeline) == 25, "the partial first hour is included"
        assert sum(b["requests"] for b in timeline) == 3
        busiest = max(timeline, key=lambda b: b["requests"])
        assert busiest["start"] == datetime(2026, 9, 27, 13, tzinfo=UTC)
        assert (busiest["requests"], busiest["failed"]) == (2, 0)

    async def test_providers_count_who_answered(
        self, session: AsyncSession, organization: Organization, seeded: None
    ) -> None:
        data = await OverviewService(session).overview(organization.id, 24, now=NOW)
        assert data["providers"] == [
            {
                "provider": "anthropic",
                "requests": 2,
                "share": 1.0,
                "avg_latency_ms": 125.0,
                "tokens": 30,
                "cost_estimate": pytest.approx(0.002),
                "models": ["anthropic-model"],
            }
        ]

    async def test_a_quiet_organization_reports_zeros(
        self, session: AsyncSession, organization_factory: OrganizationFactory, seeded: None
    ) -> None:
        quiet = await organization_factory()
        data = await OverviewService(session).overview(quiet.id, 6, now=NOW)
        assert data["requests"]["total"] == 0
        assert data["requests"]["success_rate"] is None
        assert data["requests"]["avg_latency_ms"] is None
        assert data["providers"] == []
        assert data["bucket_seconds"] == 900
        assert all(b["requests"] == 0 for b in data["timeline"])


@pytest.mark.database
@pytest.mark.redis
class TestOverviewEndpoint:
    async def test_counts_activity_across_modules(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
        await api.post("/v1/conversations", json={}, headers=headers)
        await api.delete(f"/v1/conversations/{conversation['id']}", headers=headers)
        await api.post(
            "/v1/documents",
            json={"title": "Guide", "content": "Observatory guide. " * 40},
            headers=headers,
        )
        await api.post("/v1/knowledge/search", json={"query": "guide"}, headers=headers)
        await api.post(
            "/v1/memories", json={"type": "semantic", "content": "Monochrome."}, headers=headers
        )

        response = await api.get("/v1/observatory/overview", headers=headers)
        assert response.status_code == 200, response.text
        data: dict[str, Any] = response.json()
        assert data["window_hours"] == 24
        assert data["conversations"] == {"active": 1, "total": 1}
        assert data["knowledge"]["documents"] == 1
        assert data["knowledge"]["chunks"] >= 1
        assert data["knowledge"]["indexed_in_window"] == 1
        assert data["knowledge"]["queries_in_window"] == 1
        assert data["knowledge"]["zero_result_rate"] == 0.0
        assert data["memories"] == {"total": 1}
        assert data["requests"]["total"] == 0

    async def test_validates_the_window_and_the_tenant(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        for hours in (0, 24 * 91):
            response = await api.get(
                "/v1/observatory/overview", params={"window_hours": hours}, headers=headers
            )
            assert response.status_code == 422
        stranger = {ORGANIZATION_HEADER: str(uuid.uuid4())}
        assert (await api.get("/v1/observatory/overview", headers=stranger)).status_code == 404
