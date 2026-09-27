"""Prometheus metrics.

Labels are bounded: routes are path templates (never raw paths), providers and
models come from the static catalog, and error kinds from an enum. Served on a
separate port (`CORTEX_METRICS_PORT`) so they never reach the public ingress.
"""

import logging
from threading import Thread
from wsgiref.simple_server import WSGIServer

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    start_http_server,
)

logger = logging.getLogger(__name__)

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)
_PROVIDER_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 4, 8, 15, 30, 60, 120)

BUILD_INFO = Info("cortex_build", "Running build of the Cortex API.")

HTTP_REQUESTS = Counter(
    "cortex_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "cortex_http_request_duration_seconds",
    "Time to the end of the response body, by route template.",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("cortex_http_requests_in_flight", "HTTP requests being handled now.")
HTTP_EXCEPTIONS = Counter(
    "cortex_http_exceptions_total",
    "Unhandled exceptions (HTTP 500s), by route template and exception type.",
    ["route", "exception"],
)
RATE_LIMITED = Counter("cortex_rate_limited_total", "Requests rejected with HTTP 429.")

PROVIDER_REQUESTS = Counter(
    "cortex_provider_requests_total",
    "Provider call attempts, including retries and fallbacks, by outcome.",
    ["provider", "model", "outcome"],
)
PROVIDER_LATENCY = Histogram(
    "cortex_provider_request_duration_seconds",
    "Provider call latency, successes and failures alike.",
    ["provider", "model"],
    buckets=_PROVIDER_BUCKETS,
)
PROVIDER_TOKENS = Counter(
    "cortex_provider_tokens_total",
    "Tokens reported by providers.",
    ["provider", "model", "direction"],
)
PROVIDER_COST = Counter(
    "cortex_provider_cost_usd_total",
    "Estimated spend at catalog prices.",
    ["provider", "model"],
)
PROVIDER_CIRCUIT_OPEN = Gauge(
    "cortex_provider_circuit_open",
    "1 while a provider's circuit breaker is open (calls are skipped).",
    ["provider"],
)


def set_build_info(*, version: str, release: str | None, revision: str | None, env: str) -> None:
    BUILD_INFO.info(
        {
            "version": version,
            "release": release or version,
            "revision": revision or "unknown",
            "env": env,
        }
    )


class MetricsServer:
    """The Prometheus exposition endpoint on its own port and thread."""

    def __init__(self, port: int, *, registry: CollectorRegistry = REGISTRY) -> None:
        self.port = port
        self.registry = registry
        self._server: WSGIServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        try:
            self._server, self._thread = start_http_server(self.port, registry=self.registry)
        except OSError as exc:
            # A second worker on the same host, or a reload: keep serving traffic.
            logger.warning("Metrics port %d unavailable, not exporting: %s", self.port, exc)
            return
        logger.info("Prometheus metrics on :%d/metrics", self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = self._thread = None
