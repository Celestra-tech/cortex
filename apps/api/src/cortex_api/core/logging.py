"""Logging for humans (console) and for log pipelines (one JSON object per line).

JSON records use Cloud Logging's field names (`severity`, `message`,
`logging.googleapis.com/trace`) so severity, request correlation, and the
link to the trace work without a log-router config; other backends read them
as plain JSON. Every record carries the current request ID and trace ID.
"""

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from cortex_api.core.config import LogFormat, LogLevel
from cortex_api.core.request_id import current_request_id

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
    | {"message", "asctime", "taskName", "request_id", "color_message"}
)


class RequestContextFilter(logging.Filter):
    """Exposes the request ID as `%(request_id)s` for text formats."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def __init__(
        self, *, static_fields: dict[str, str] | None = None, gcp_project_id: str | None = None
    ) -> None:
        super().__init__()
        self.static_fields = {k: v for k, v in (static_fields or {}).items() if v}
        self.gcp_project_id = gcp_project_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = current_request_id.get()
        if request_id:
            payload["request_id"] = request_id

        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            trace_id, span_id = format(span.trace_id, "032x"), format(span.span_id, "016x")
            payload["trace_id"], payload["span_id"] = trace_id, span_id
            if self.gcp_project_id:
                payload["logging.googleapis.com/trace"] = (
                    f"projects/{self.gcp_project_id}/traces/{trace_id}"
                )
                payload["logging.googleapis.com/spanId"] = span_id
                payload["logging.googleapis.com/trace_sampled"] = span.trace_flags.sampled

        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        for key, value in self.static_fields.items():
            payload.setdefault(key, value)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    level: LogLevel,
    log_format: LogFormat = "console",
    *,
    static_fields: dict[str, str] | None = None,
    gcp_project_id: str | None = None,
) -> None:
    formatter: dict[str, Any] = (
        {
            "()": JsonFormatter,
            "static_fields": static_fields or {},
            "gcp_project_id": gcp_project_id,
        }
        if log_format == "json"
        else {
            "format": "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }
    )
    handler = {"level": level, "handlers": ["default"], "propagate": False}
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_context": {"()": RequestContextFilter}},
            "formatters": {"default": formatter},
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_context"],
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "cortex_api": handler,
                "uvicorn": handler,
                "uvicorn.error": handler,
                # Replaced by the structured access log in cortex_api.core.http.
                "uvicorn.access": {**handler, "level": "WARNING"},
            },
            # Third-party libraries: warnings and errors only, in the same format.
            "root": {"level": "WARNING", "handlers": ["default"]},
        }
    )
