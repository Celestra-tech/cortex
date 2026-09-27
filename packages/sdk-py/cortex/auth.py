"""Credentials, tenant headers, and request IDs."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

from .errors import CortexError

ORGANIZATION_HEADER = "X-Organization-ID"
REQUEST_ID_HEADER = "X-Request-ID"
DEFAULT_BASE_URL = "http://localhost:8000"

ApiKey = str | Callable[[], str]
"""A static key, or a callable for keys that rotate (called before every attempt)."""


class NotGiven:
    """Sentinel for "argument omitted" where `None` is meaningful (fall back to env vs. disable)."""

    def __repr__(self) -> str:
        return "NOT_GIVEN"


NOT_GIVEN = NotGiven()


def env(name: str) -> str | None:
    return os.environ.get(name) or None


class Credentials:
    """`Authorization: Bearer <api key>` plus an optional `X-Organization-ID`.

    With an API key, the organization header must match the key's
    organization. Without one it names the tenant, which only development
    servers accept.
    """

    def __init__(
        self,
        api_key: ApiKey | NotGiven | None = NOT_GIVEN,
        organization_id: str | NotGiven | None = NOT_GIVEN,
    ) -> None:
        self._api_key = env("CORTEX_API_KEY") if isinstance(api_key, NotGiven) else api_key
        self.organization_id = (
            env("CORTEX_ORGANIZATION_ID")
            if isinstance(organization_id, NotGiven)
            else organization_id
        )

    @property
    def has_api_key(self) -> bool:
        return self._api_key is not None

    def headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._api_key is not None:
            key = self._api_key() if callable(self._api_key) else self._api_key
            if not key:
                raise CortexError("The api_key callable returned an empty key")
            headers["Authorization"] = f"Bearer {key}"
        if self.organization_id:
            headers[ORGANIZATION_HEADER] = self.organization_id
        return headers


def create_request_id() -> str:
    """`req_` + a UUID: unique per logical call and reused across its retries."""
    return f"req_{uuid.uuid4().hex}"
