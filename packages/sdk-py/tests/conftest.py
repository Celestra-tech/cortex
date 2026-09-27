from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import httpx
import pytest

from cortex import AsyncCortex, Cortex

from .helpers import API_KEY, BASE_URL, Server, Sleeps


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CORTEX_API_KEY", "CORTEX_BASE_URL", "CORTEX_ORGANIZATION_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def make_client(sleeps: Sleeps) -> Iterator[Callable[..., Cortex]]:
    clients: list[Cortex] = []

    def make(server: Server, **options: Any) -> Cortex:
        options.setdefault("api_key", API_KEY)
        options.setdefault("organization_id", None)
        options.setdefault("base_url", BASE_URL)
        client = Cortex(http_client=httpx.Client(transport=httpx.MockTransport(server)), **options)
        client._transport.sleep = sleeps
        clients.append(client)
        return client

    yield make
    for client in clients:
        client._http.close()


@pytest.fixture
async def make_async_client(sleeps: Sleeps) -> AsyncIterator[Callable[..., AsyncCortex]]:
    clients: list[AsyncCortex] = []

    def make(server: Server, **options: Any) -> AsyncCortex:
        options.setdefault("api_key", API_KEY)
        options.setdefault("organization_id", None)
        options.setdefault("base_url", BASE_URL)
        client = AsyncCortex(
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(server)), **options
        )
        client._transport.sleep = sleeps.asleep
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client._http.aclose()
