import json
import logging
import sys

import httpx2
import pytest
from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from prometheus_client import REGISTRY
from redis.asyncio import Redis

from cortex_api.cache.redis import get_redis
from cortex_api.core.logging import JsonFormatter
from cortex_api.core.request_id import current_request_id


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


class TestJsonLogs:
    def record(
        self, message: str = "hello %s", *args: object, **extra: object
    ) -> dict[str, object]:
        record = logging.LogRecord(
            "cortex_api.x", logging.WARNING, __file__, 1, message, args, None
        )
        for key, value in extra.items():
            setattr(record, key, value)
        formatter = JsonFormatter(static_fields={"service": "cortex-api", "release": "1.0.0"})
        parsed: dict[str, object] = json.loads(formatter.format(record))
        return parsed

    def test_fields_follow_cloud_logging(self) -> None:
        entry = self.record("hello %s", "world")
        assert entry["severity"] == "WARNING"
        assert entry["message"] == "hello world"
        assert entry["logger"] == "cortex_api.x"
        assert entry["service"] == "cortex-api" and entry["release"] == "1.0.0"
        assert str(entry["timestamp"]).endswith("+00:00")

    def test_request_id_and_extras_are_included(self) -> None:
        token = current_request_id.set("req-42")
        try:
            entry = self.record(httpRequest={"status": 200}, route="/v1/x")
        finally:
            current_request_id.reset(token)
        assert entry["request_id"] == "req-42"
        assert entry["httpRequest"] == {"status": 200}
        assert entry["route"] == "/v1/x"

    def test_trace_context_links_to_cloud_trace(self) -> None:
        provider = TracerProvider()
        formatter = JsonFormatter(gcp_project_id="celestra-prod")
        record = logging.LogRecord("cortex_api", logging.INFO, __file__, 1, "in span", None, None)
        with provider.get_tracer("t").start_as_current_span("s") as span:
            entry = json.loads(formatter.format(record))
            trace_id = format(span.get_span_context().trace_id, "032x")
        assert entry["trace_id"] == trace_id
        assert entry["logging.googleapis.com/trace"] == f"projects/celestra-prod/traces/{trace_id}"

    def test_exceptions_are_serialized(self) -> None:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            record = logging.LogRecord(
                "cortex_api", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
            )
        entry = json.loads(JsonFormatter().format(record))
        assert "RuntimeError: boom" in entry["exception"]


@pytest.mark.database
@pytest.mark.redis
class TestHttpMetricsAndHealth:
    async def test_requests_are_counted_by_route_template(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        route = "/v1/conversations/{conversation_id}"
        before = sample("cortex_http_requests_total", method="GET", route=route, status="404")
        missing = "01a0e253-3d76-750f-b51c-e1d5a0dd7e8e"
        await api.get(f"/v1/conversations/{missing}", headers=headers)
        after = sample("cortex_http_requests_total", method="GET", route=route, status="404")
        assert after == before + 1
        assert sample("cortex_http_request_duration_seconds_count", method="GET", route=route) >= 1

    async def test_unknown_paths_share_one_label(self, api: httpx2.AsyncClient) -> None:
        before = sample("cortex_http_requests_total", method="GET", route="unmatched", status="404")
        await api.get("/no/such/path/12345")
        await api.get("/another/unknown")
        assert (
            sample("cortex_http_requests_total", method="GET", route="unmatched", status="404")
            == before + 2
        )

    async def test_access_log_records_each_request(
        self, api: httpx2.AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="cortex_api.access"):
            await api.get("/v1/organization", headers={"X-Request-ID": "log-1"})
        (record,) = [r for r in caplog.records if r.name == "cortex_api.access"]
        assert record.getMessage().startswith("GET /v1/organization 401")
        assert record.httpRequest["status"] == 401  # type: ignore[attr-defined]
        assert record.route == "/v1/organization"  # type: ignore[attr-defined]

    async def test_redis_health(self, api: httpx2.AsyncClient) -> None:
        response = await api.get("/health/redis")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy" and body["redis"] == "connected"
        assert body["latency_ms"] >= 0

    async def test_redis_health_reports_outages(
        self, api: httpx2.AsyncClient, app: FastAPI
    ) -> None:
        broken = Redis.from_url("redis://127.0.0.1:1/15", socket_connect_timeout=0.2)
        app.dependency_overrides[get_redis] = lambda: broken
        try:
            response = await api.get("/health/redis")
        finally:
            await broken.aclose()
        assert response.status_code == 503
        assert response.json()["redis"] == "disconnected"
