"""Drives an ASGI WebSocket endpoint on the test's own event loop.

Starlette's TestClient runs the app in a separate thread and loop, which would
strand the loop-bound session and Redis fixtures the `app` fixture shares.
"""

import asyncio
import json
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlencode

from fastapi import FastAPI


class WebSocketClosedError(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"closed {code}: {reason}")
        self.code = code
        self.reason = reason


class WebSocketSession:
    def __init__(
        self,
        app: FastAPI,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
    ) -> None:
        self.app = app
        self.subprotocol: str | None = None
        self.scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "ws",
            "http_version": "1.1",
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": urlencode(params or {}).encode(),
            "headers": [
                (b"host", b"cortex.test"),
                *((k.lower().encode(), v.encode()) for k, v in (headers or {}).items()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("cortex.test", 80),
            "subprotocols": list(subprotocols or []),
            "state": {},
        }
        self._inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._outbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(
            self.app(self.scope, self._inbound.get, self._outbound.put)  # type: ignore[arg-type]
        )
        await self._inbound.put({"type": "websocket.connect"})
        first = await self._next()
        if first["type"] == "websocket.close":
            await self._finish()
            raise WebSocketClosedError(first.get("code", 1000), first.get("reason", ""))
        assert first["type"] == "websocket.accept", first
        self.subprotocol = first.get("subprotocol")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._inbound.put({"type": "websocket.disconnect", "code": 1000})
        await self._finish()

    async def receive_json(self, wait: float = 2.0) -> dict[str, Any]:
        message = await self._next(wait)
        if message["type"] == "websocket.close":
            raise WebSocketClosedError(message.get("code", 1000), message.get("reason", ""))
        assert message["type"] == "websocket.send", message
        frame: dict[str, Any] = json.loads(message["text"])
        return frame

    async def receive_event(self, wait: float = 2.0) -> dict[str, Any]:
        """Next frame that is not a heartbeat."""
        while (frame := await self.receive_json(wait))["type"] == "stream.ping":
            pass
        return frame

    async def _next(self, wait: float = 2.0) -> dict[str, Any]:
        assert self._task is not None
        if not self._outbound.empty():
            return self._outbound.get_nowait()
        get = asyncio.ensure_future(self._outbound.get())
        done, _ = await asyncio.wait(
            {get, self._task}, timeout=wait, return_when=asyncio.FIRST_COMPLETED
        )
        if get in done:
            return get.result()
        get.cancel()
        if not self._outbound.empty():
            return self._outbound.get_nowait()
        if self._task in done:
            self._task.result()
            raise AssertionError("the endpoint returned without sending")
        raise TimeoutError("no WebSocket message arrived")

    async def _finish(self) -> None:
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=5)
