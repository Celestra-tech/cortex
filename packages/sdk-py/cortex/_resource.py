from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from urllib.parse import quote

from ._transport import AsyncTransport, SyncTransport
from .models import Page


class SyncAPIResource:
    def __init__(self, transport: SyncTransport) -> None:
        self._transport = transport


class AsyncAPIResource:
    def __init__(self, transport: AsyncTransport) -> None:
        self._transport = transport


def segment(value: str) -> str:
    """One URL path segment."""
    return quote(value, safe="")


def paginate[T](fetch: Callable[[int, int], Page[T]], *, limit: int, offset: int) -> Iterator[T]:
    """Yields every item across pages, fetching lazily."""
    cursor = offset
    while True:
        page = fetch(limit, cursor)
        yield from page.items
        cursor += len(page.items)
        if not page.items or cursor >= page.total:
            return


async def apaginate[T](
    fetch: Callable[[int, int], Awaitable[Page[T]]], *, limit: int, offset: int
) -> AsyncIterator[T]:
    cursor = offset
    while True:
        page = await fetch(limit, cursor)
        for item in page.items:
            yield item
        cursor += len(page.items)
        if not page.items or cursor >= page.total:
            return
