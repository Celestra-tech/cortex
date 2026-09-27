from __future__ import annotations

from typing import Any, Literal

from ._resource import AsyncAPIResource, SyncAPIResource, segment
from ._transport import Call, RequestOptions
from .models import ApiKey, ApiKeyList, ApiKeyWithSecret

ApiKeyRole = Literal["admin", "member"]


def _list(include_revoked: bool | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="api_keys.list",
        path="/v1/api-keys",
        params={"include_revoked": include_revoked},
        options=options,
    )


def _get(id: str, options: RequestOptions | None) -> Call:
    return Call(operation="api_keys.get", path=f"/v1/api-keys/{segment(id)}", options=options)


def _no_retry(options: RequestOptions | None) -> RequestOptions:
    # A retry after a lost response would mint a second live key.
    return {"max_retries": 0, **(options or {})}


def _create(
    name: str, role: ApiKeyRole, expires_in_days: int | None, options: RequestOptions | None
) -> Call:
    body: dict[str, Any] = {"name": name, "role": role}
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    return Call(
        operation="api_keys.create",
        method="POST",
        path="/v1/api-keys",
        json=body,
        options=_no_retry(options),
    )


def _rotate(
    id: str,
    grace_period_seconds: int | None,
    expires_in_days: int | None,
    options: RequestOptions | None,
) -> Call:
    body: dict[str, Any] = {}
    if grace_period_seconds is not None:
        body["grace_period_seconds"] = grace_period_seconds
    if expires_in_days is not None:
        body["expires_in_days"] = expires_in_days
    return Call(
        operation="api_keys.rotate",
        method="POST",
        path=f"/v1/api-keys/{segment(id)}/rotate",
        json=body,
        options=_no_retry(options),
    )


def _revoke(id: str, options: RequestOptions | None) -> Call:
    return Call(
        operation="api_keys.revoke",
        method="DELETE",
        path=f"/v1/api-keys/{segment(id)}",
        options=options,
    )


class ApiKeys(SyncAPIResource):
    """The organization's API keys. Requires an admin key.

    Secrets are returned only by `create` and `rotate`, which are never retried
    automatically.
    """

    def list(
        self, *, include_revoked: bool | None = None, request_options: RequestOptions | None = None
    ) -> ApiKeyList:
        """Newest first."""
        return self._transport.request(_list(include_revoked, request_options), ApiKeyList)

    def get(self, id: str, *, request_options: RequestOptions | None = None) -> ApiKey:
        return self._transport.request(_get(id, request_options), ApiKey)

    def create(
        self,
        *,
        name: str,
        role: ApiKeyRole = "member",
        expires_in_days: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ApiKeyWithSecret:
        """Mint a key. Store `secret` now; it cannot be retrieved again."""
        call = _create(name, role, expires_in_days, request_options)
        return self._transport.request(call, ApiKeyWithSecret)

    def rotate(
        self,
        id: str,
        *,
        grace_period_seconds: int | None = None,
        expires_in_days: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ApiKeyWithSecret:
        """Replace a key (same name and role). The old key keeps working for the
        grace period (API default 3600s; 0 revokes it immediately)."""
        call = _rotate(id, grace_period_seconds, expires_in_days, request_options)
        return self._transport.request(call, ApiKeyWithSecret)

    def revoke(self, id: str, *, request_options: RequestOptions | None = None) -> ApiKey:
        """Revoke immediately. The last usable admin key cannot be revoked (ConflictError)."""
        return self._transport.request(_revoke(id, request_options), ApiKey)


class AsyncApiKeys(AsyncAPIResource):
    async def list(
        self, *, include_revoked: bool | None = None, request_options: RequestOptions | None = None
    ) -> ApiKeyList:
        return await self._transport.request(_list(include_revoked, request_options), ApiKeyList)

    async def get(self, id: str, *, request_options: RequestOptions | None = None) -> ApiKey:
        return await self._transport.request(_get(id, request_options), ApiKey)

    async def create(
        self,
        *,
        name: str,
        role: ApiKeyRole = "member",
        expires_in_days: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ApiKeyWithSecret:
        call = _create(name, role, expires_in_days, request_options)
        return await self._transport.request(call, ApiKeyWithSecret)

    async def rotate(
        self,
        id: str,
        *,
        grace_period_seconds: int | None = None,
        expires_in_days: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> ApiKeyWithSecret:
        call = _rotate(id, grace_period_seconds, expires_in_days, request_options)
        return await self._transport.request(call, ApiKeyWithSecret)

    async def revoke(self, id: str, *, request_options: RequestOptions | None = None) -> ApiKey:
        return await self._transport.request(_revoke(id, request_options), ApiKey)
