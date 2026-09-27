from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from .models import Execution, ModelList, Page

ExecutionPage = Page[Execution]


def _models(options: RequestOptions | None) -> Call:
    return Call(operation="router.models", path="/v1/models", options=options)


def _list_executions(
    provider: str | None,
    model: str | None,
    success: bool | None,
    completion_id: str | None,
    limit: int | None,
    offset: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="router.list_executions",
        path="/v1/executions",
        params={
            "provider": provider,
            "model": model,
            "success": success,
            "completion_id": completion_id,
            "limit": limit,
            "offset": offset,
        },
        options=options,
    )


def _get_execution(id: str, options: RequestOptions | None) -> Call:
    return Call(
        operation="router.get_execution", path=f"/v1/executions/{segment(id)}", options=options
    )


class Router(SyncAPIResource):
    """The model catalog and the append-only log of provider calls."""

    def models(self, *, request_options: RequestOptions | None = None) -> ModelList:
        """Every catalog model with live availability and this organization's
        policy verdict (`allowed`)."""
        return self._transport.request(_models(request_options), ModelList)

    def list_executions(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        success: bool | None = None,
        completion_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ExecutionPage:
        """Newest first. One completion with fallbacks yields several executions."""
        call = _list_executions(
            provider, model, success, completion_id, limit, offset, request_options
        )
        return self._transport.request(call, ExecutionPage)

    def iter_executions(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        success: bool | None = None,
        completion_id: str | None = None,
        page_size: int = 100,
    ) -> Iterator[Execution]:
        return paginate(
            lambda limit, offset: self.list_executions(
                provider=provider,
                model=model,
                success=success,
                completion_id=completion_id,
                limit=limit,
                offset=offset,
            ),
            limit=page_size,
            offset=0,
        )

    def get_execution(self, id: str, *, request_options: RequestOptions | None = None) -> Execution:
        return self._transport.request(_get_execution(id, request_options), Execution)


class AsyncRouter(AsyncAPIResource):
    async def models(self, *, request_options: RequestOptions | None = None) -> ModelList:
        return await self._transport.request(_models(request_options), ModelList)

    async def list_executions(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        success: bool | None = None,
        completion_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ExecutionPage:
        call = _list_executions(
            provider, model, success, completion_id, limit, offset, request_options
        )
        return await self._transport.request(call, ExecutionPage)

    def iter_executions(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        success: bool | None = None,
        completion_id: str | None = None,
        page_size: int = 100,
    ) -> AsyncIterator[Execution]:
        return apaginate(
            lambda limit, offset: self.list_executions(
                provider=provider,
                model=model,
                success=success,
                completion_id=completion_id,
                limit=limit,
                offset=offset,
            ),
            limit=page_size,
            offset=0,
        )

    async def get_execution(
        self, id: str, *, request_options: RequestOptions | None = None
    ) -> Execution:
        return await self._transport.request(_get_execution(id, request_options), Execution)
