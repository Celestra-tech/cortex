import asyncio
import contextlib
import json
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError

from cortex_api.api.deps import RedisDep, SettingsDep, authenticate
from cortex_api.core.security import AuthenticationError, OrganizationMismatchError
from cortex_api.database.session import DbSession
from cortex_api.repositories.base import NotFoundError
from cortex_api.services.observatory.events import event_channel, subscription

logger = logging.getLogger(__name__)

router = APIRouter(tags=["events"])

EVENTS_PROTOCOL = "cortex.events.v1"
BEARER_PROTOCOL_PREFIX = "cortex.bearer."


def _frame(type_: str, **data: object) -> str:
    return json.dumps({"type": type_, "data": data}, default=str, separators=(",", ":"))


def _credentials(websocket: WebSocket) -> tuple[str | None, str | None]:
    """The `Authorization` value to check and the subprotocol to answer with.

    Browsers cannot set headers on a WebSocket, so they offer the key as a
    `cortex.bearer.<key>` subprotocol next to `cortex.events.v1`. The server
    answers with `cortex.events.v1` only, never echoing the key back.
    """
    offered: list[str] = list(websocket.scope.get("subprotocols") or [])
    selected = EVENTS_PROTOCOL if EVENTS_PROTOCOL in offered else None
    for protocol in offered:
        if protocol.startswith(BEARER_PROTOCOL_PREFIX):
            return f"Bearer {protocol.removeprefix(BEARER_PROTOCOL_PREFIX)}", selected
    return websocket.headers.get("authorization"), selected


async def _refuse(websocket: WebSocket, subprotocol: str | None, reason: str) -> None:
    # Accept first: a close during the handshake reaches browsers as a bare 1006,
    # and clients need 1008 to know that retrying is pointless.
    await websocket.accept(subprotocol=subprotocol)
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=reason)


@router.websocket("/events")
async def stream_events(
    websocket: WebSocket,
    session: DbSession,
    redis: RedisDep,
    settings: SettingsDep,
    organization_id: Annotated[uuid.UUID | None, Query()] = None,
) -> None:
    """Live operational events for one organization.

    Authenticated like any `/v1` call: an API key, as a `cortex.bearer.<key>`
    subprotocol or an `Authorization` header, names the organization, and an
    `organization_id` query parameter must match it. Without a key the query
    parameter alone names the tenant, which is refused when API keys are
    required. Browser connections must come from an allowed CORS origin.
    Frames are JSON: `stream.ready` once, then events as published, with
    `stream.ping` after each quiet `events_heartbeat_seconds`.
    """
    authorization, subprotocol = _credentials(websocket)
    origin = websocket.headers.get("origin")
    if origin and "*" not in settings.api_cors_origins and origin not in settings.api_cors_origins:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="origin not allowed")
        return

    try:
        principal = await authenticate(session, settings, authorization, organization_id)
    except AuthenticationError as exc:
        reason = (
            str(exc) if authorization else "An API key is required: offer the key as a subprotocol"
        )
        await _refuse(websocket, subprotocol, reason[:120])
        return
    except OrganizationMismatchError:
        await _refuse(websocket, subprotocol, "API key does not belong to the organization")
        return
    except NotFoundError:
        await _refuse(websocket, subprotocol, "unknown organization")
        return
    finally:
        # Release the pooled connection: the socket may stay open for hours.
        await session.commit()
    organization = principal.organization

    channel = event_channel(settings.events_key_prefix, organization.id)
    try:
        async with subscription(redis, channel) as pubsub:
            await websocket.accept(subprotocol=subprotocol)
            await websocket.send_text(_frame("stream.ready", organization_id=organization.id))
            await _pump(websocket, pubsub, settings.events_heartbeat_seconds)
    except (RedisError, OSError) as exc:
        logger.warning("Event stream for %s lost Redis: %r", organization.id, exc)
        # 1011 tells the client to reconnect with backoff.
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await websocket.close(
                code=status.WS_1011_INTERNAL_ERROR, reason="event bus unavailable"
            )


async def _pump(websocket: WebSocket, pubsub: PubSub, heartbeat_seconds: float) -> None:
    """Forwards published events until the client disconnects."""

    async def forward() -> None:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=heartbeat_seconds
            )
            if message is None:
                await websocket.send_text(_frame("stream.ping"))
                continue
            data = message["data"]
            await websocket.send_text(data.decode() if isinstance(data, bytes) else data)

    async def until_disconnect() -> None:
        # Clients have nothing to say; reading is how a close is noticed.
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(forward()), asyncio.create_task(until_disconnect())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    for task in done:
        exc = task.exception()
        if exc is not None and not isinstance(exc, WebSocketDisconnect | RuntimeError):
            raise exc
