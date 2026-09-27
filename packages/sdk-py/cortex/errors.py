"""Typed errors. `except CortexError` catches everything the SDK raises."""

from __future__ import annotations

import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class CortexError(Exception):
    """Root of every error the SDK raises."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    """Dotted path to the offending field, e.g. `messages.0.content`."""
    message: str


class APIError(CortexError):
    """The API answered with an error status."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None,
        body: Any = None,
        request_id: str | None = None,
        headers: httpx.Headers | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.body = body
        self.request_id = request_id
        """Quote this when reporting a problem; it matches the server's `X-Request-ID`."""
        self.headers = headers

    @property
    def detail(self) -> str | None:
        detail = self.body.get("detail") if isinstance(self.body, dict) else None
        return detail if isinstance(detail, str) else None


class AuthenticationError(APIError):
    """401: missing, malformed, invalid, or revoked API key."""


class PermissionDeniedError(APIError):
    """403: the key's organization differs from the one requested, or policy forbids it."""


class NotFoundError(APIError):
    pass


class ConflictError(APIError):
    """409. For duplicate documents, `document_id` is the existing copy."""

    @property
    def document_id(self) -> str | None:
        value = self.body.get("document_id") if isinstance(self.body, dict) else None
        return value if isinstance(value, str) else None


class ValidationError(APIError):
    """Invalid request: rejected by the server (400, 413, 415, 422) or, with
    `status=None`, caught by the SDK before anything was sent."""

    def __init__(
        self, message: str, *, issues: list[ValidationIssue] | None = None, **kwargs: Any
    ) -> None:
        kwargs.setdefault("status", None)
        super().__init__(message, **kwargs)
        self.issues = issues if issues is not None else issues_from_body(self.body)


class RateLimitError(APIError):
    """429, after the SDK's own retries."""

    @property
    def retry_after(self) -> float | None:
        """The server's latest hint, in seconds."""
        return retry_after_seconds(self.headers) if self.headers is not None else None


class ProviderError(APIError):
    """502/503: no model provider could serve the request.

    For completions the server has already retried and fallen back across
    providers; `attempts` lists every provider call it made.
    """

    @property
    def completion_id(self) -> str | None:
        value = self.body.get("id") if isinstance(self.body, dict) else None
        return value if isinstance(value, str) else None

    @property
    def attempts(self) -> list[dict[str, Any]]:
        value = self.body.get("attempts") if isinstance(self.body, dict) else None
        return value if isinstance(value, list) else []


class InternalServerError(APIError):
    """Any other 5xx."""


class NetworkError(CortexError):
    """No response: DNS, connection, TLS, or a dropped stream."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class RequestTimeoutError(NetworkError):
    """No response within the timeout."""


class ResponseValidationError(CortexError):
    """The API answered successfully but not in the documented shape."""

    def __init__(self, message: str, *, issues: list[ValidationIssue], body: Any) -> None:
        super().__init__(message)
        self.issues = issues
        self.body = body


_BY_STATUS: dict[int, type[APIError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    413: ValidationError,
    415: ValidationError,
    422: ValidationError,
    429: RateLimitError,
    502: ProviderError,
    503: ProviderError,
}


def error_from_response(
    status: int, body: Any, headers: httpx.Headers, request_id: str | None, operation: str
) -> APIError:
    """Maps an error response to the most specific error class."""
    detail = describe(body)
    message = f"{operation} failed with {status}" + (f": {detail}" if detail else "")
    cls = _BY_STATUS.get(status) or (InternalServerError if status >= 500 else APIError)
    return cls(
        message,
        status=status,
        body=body,
        headers=headers,
        request_id=headers.get("x-request-id") or request_id,
    )


def retry_after_seconds(headers: httpx.Headers) -> float | None:
    """`retry-after-ms`, or `Retry-After` as seconds or an HTTP date."""
    if (ms := headers.get("retry-after-ms")) is not None:
        try:
            return max(0.0, float(ms) / 1000)
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def describe(body: Any) -> str | None:
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return str(body["detail"])
    issues = issues_from_body(body)
    return "; ".join(f"{i.path or 'body'}: {i.message}" for i in issues) or None


def issues_from_body(body: Any) -> list[ValidationIssue]:
    """FastAPI's `{"detail": [{"loc": [...], "msg": "..."}]}` validation shape."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, list):
        return []
    issues = []
    for item in detail:
        if not isinstance(item, dict) or not isinstance(item.get("msg"), str):
            continue
        loc = item.get("loc")
        path = ".".join(str(p) for p in loc if p != "body") if isinstance(loc, list) else ""
        issues.append(ValidationIssue(path=path, message=item["msg"]))
    return issues
