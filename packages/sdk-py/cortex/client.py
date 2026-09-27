from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self

import httpx

from ._transport import AsyncTransport, RetryConfig, SyncTransport
from .api_keys import ApiKeys, AsyncApiKeys
from .auth import DEFAULT_BASE_URL, NOT_GIVEN, ApiKey, Credentials, NotGiven, env
from .chat import AsyncChat, Chat
from .documents import AsyncDocuments, Documents
from .evidence import AsyncEvidence, Evidence
from .knowledge import AsyncKnowledge, Knowledge
from .memory import AsyncMemoryResource, MemoryResource
from .middleware import AsyncMiddlewareFunction, Middleware, MiddlewareFunction
from .router import AsyncRouter, Router
from .system import AsyncSystem, System

SDK_VERSION = "1.0.0a0"
DEFAULT_TIMEOUT = 60.0


class _BaseClient:
    def __init__(
        self,
        *,
        api_key: ApiKey | NotGiven | None,
        base_url: str | None,
        organization_id: str | NotGiven | None,
        timeout: float,
        max_retries: int | None,
        retry: RetryConfig | None,
        default_headers: Mapping[str, str] | None,
        validate_responses: bool,
    ) -> None:
        self.base_url = (base_url or env("CORTEX_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._credentials = Credentials(api_key, organization_id)
        retry = retry or RetryConfig()
        if max_retries is not None:
            retry = RetryConfig(max_retries, retry.initial_delay, retry.max_delay)
        self._core: dict[str, Any] = {
            "base_url": self.base_url,
            "credentials": self._credentials,
            "timeout": timeout,
            "retry": retry,
            "headers": {"X-Cortex-Client": f"cortex-py/{SDK_VERSION}", **(default_headers or {})},
            "validate_responses": validate_responses,
        }
        self._settings: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url,
            "organization_id": organization_id,
            "timeout": timeout,
            "max_retries": max_retries,
            "retry": retry,
            "default_headers": default_headers,
            "validate_responses": validate_responses,
        }

    @property
    def organization_id(self) -> str | None:
        return self._credentials.organization_id


class Cortex(_BaseClient):
    """The synchronous Cortex client.

    ```python
    cortex = Cortex(api_key="ctx_...", base_url="https://cortex.example.com")
    completion = cortex.chat.complete(messages=[{"role": "user", "content": "Hi"}])
    ```

    Omitted `api_key`, `base_url`, and `organization_id` fall back to
    `CORTEX_API_KEY`, `CORTEX_BASE_URL`, and `CORTEX_ORGANIZATION_ID`; pass
    None to send no credential at all. Close the client (or use it as a
    context manager) to release its connection pool.
    """

    def __init__(
        self,
        *,
        api_key: ApiKey | NotGiven | None = NOT_GIVEN,
        base_url: str | None = None,
        organization_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int | None = None,
        retry: RetryConfig | None = None,
        middleware: Sequence[Middleware | MiddlewareFunction] = (),
        default_headers: Mapping[str, str] | None = None,
        validate_responses: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            organization_id=organization_id,
            timeout=timeout,
            max_retries=max_retries,
            retry=retry,
            default_headers=default_headers,
            validate_responses=validate_responses,
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client()
        self._middleware = list(middleware)
        self._transport = SyncTransport(http=self._http, middleware=self._middleware, **self._core)
        self.chat = Chat(self._transport)
        self.memory = MemoryResource(self._transport)
        self.knowledge = Knowledge(self._transport)
        self.documents = Documents(self._transport)
        self.evidence = Evidence(self._transport)
        self.router = Router(self._transport)
        self.system = System(self._transport)
        self.api_keys = ApiKeys(self._transport)

    def use(self, middleware: Middleware | MiddlewareFunction) -> Self:
        """Appends middleware (innermost) to this client."""
        self._middleware.append(middleware)
        return self

    def with_options(self, **overrides: Any) -> Cortex:
        """A client with some settings replaced, sharing this one's connection
        pool and middleware: `cortex.with_options(timeout=5, max_retries=0)`."""
        settings = {**self._settings, **overrides}
        return Cortex(**settings, middleware=self._middleware, http_client=self._http)

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class AsyncCortex(_BaseClient):
    """The asyncio Cortex client; every method is a coroutine.

    ```python
    async with AsyncCortex(api_key="ctx_...") as cortex:
        completion = await cortex.chat.complete(messages=[{"role": "user", "content": "Hi"}])
    ```
    """

    def __init__(
        self,
        *,
        api_key: ApiKey | NotGiven | None = NOT_GIVEN,
        base_url: str | None = None,
        organization_id: str | NotGiven | None = NOT_GIVEN,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int | None = None,
        retry: RetryConfig | None = None,
        middleware: Sequence[Middleware | AsyncMiddlewareFunction] = (),
        default_headers: Mapping[str, str] | None = None,
        validate_responses: bool = True,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            organization_id=organization_id,
            timeout=timeout,
            max_retries=max_retries,
            retry=retry,
            default_headers=default_headers,
            validate_responses=validate_responses,
        )
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient()
        self._middleware = list(middleware)
        self._transport = AsyncTransport(http=self._http, middleware=self._middleware, **self._core)
        self.chat = AsyncChat(self._transport)
        self.memory = AsyncMemoryResource(self._transport)
        self.knowledge = AsyncKnowledge(self._transport)
        self.documents = AsyncDocuments(self._transport)
        self.evidence = AsyncEvidence(self._transport)
        self.router = AsyncRouter(self._transport)
        self.system = AsyncSystem(self._transport)
        self.api_keys = AsyncApiKeys(self._transport)

    def use(self, middleware: Middleware | AsyncMiddlewareFunction) -> Self:
        self._middleware.append(middleware)
        return self

    def with_options(self, **overrides: Any) -> AsyncCortex:
        settings = {**self._settings, **overrides}
        return AsyncCortex(**settings, middleware=self._middleware, http_client=self._http)

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
