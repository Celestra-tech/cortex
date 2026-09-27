import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi import FastAPI, status
from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.core.security import generate_api_key, hash_api_key
from cortex_api.models.organization import Organization
from cortex_api.repositories.api_key import ApiKeyRepository
from cortex_api.services.observatory.events import EventPublisher, EventType

from ..conftest import OrganizationFactory
from .conftest import HEARTBEAT_SECONDS
from .websocket import WebSocketClosedError, WebSocketSession

pytestmark = [pytest.mark.database, pytest.mark.redis]

ALLOWED_ORIGIN = "http://localhost:3000"


def connect(
    app: FastAPI,
    organization_id: uuid.UUID | str | None,
    origin: str | None = ALLOWED_ORIGIN,
    *,
    headers: dict[str, str] | None = None,
    subprotocols: list[str] | None = None,
) -> WebSocketSession:
    return WebSocketSession(
        app,
        "/v1/events",
        params={} if organization_id is None else {"organization_id": str(organization_id)},
        headers={**({"origin": origin} if origin else {}), **(headers or {})},
        subprotocols=subprotocols,
    )


def bearer_protocols(secret: str) -> list[str]:
    return ["cortex.events.v1", f"cortex.bearer.{secret}"]


async def test_stream_opens_with_a_ready_frame(app: FastAPI, organization: Organization) -> None:
    async with connect(app, organization.id) as socket:
        ready = await socket.receive_json()
    assert ready == {"type": "stream.ready", "data": {"organization_id": str(organization.id)}}


async def test_published_events_are_forwarded_verbatim(
    app: FastAPI, organization: Organization, redis: Redis, redis_key_prefix: str
) -> None:
    publisher = EventPublisher(redis, prefix=redis_key_prefix)
    async with connect(app, organization.id) as socket:
        await socket.receive_json()
        await publisher.publish(uuid.uuid4(), EventType.MEMORY_STORED, {"tenant": "other"})
        sent = await publisher.publish(organization.id, EventType.DOCUMENT_INDEXED, {"n": 1})
        frame = await socket.receive_event()
    assert frame["id"] == str(sent.id)
    assert frame["type"] == "document.indexed"
    assert frame["data"] == {"n": 1}


async def test_quiet_streams_send_heartbeats(app: FastAPI, organization: Organization) -> None:
    async with connect(app, organization.id) as socket:
        await socket.receive_json()
        ping = await socket.receive_json(wait=HEARTBEAT_SECONDS * 5)
    assert ping == {"type": "stream.ping", "data": {}}


async def test_completions_stream_live(
    app: FastAPI, api: httpx2.AsyncClient, headers: dict[str, str], organization: Organization
) -> None:
    async with connect(app, organization.id) as socket:
        await socket.receive_json()
        await api.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hello"}]},
            headers=headers,
        )
        received = await socket.receive_event()
        completed = await socket.receive_event()
    assert (received["type"], completed["type"]) == ("request.received", "execution.completed")


async def test_foreign_origins_are_refused_during_the_handshake(
    app: FastAPI, organization: Organization
) -> None:
    with pytest.raises(WebSocketClosedError) as closed:
        async with connect(app, organization.id, "http://evil.test"):
            pass
    assert closed.value.code == status.WS_1008_POLICY_VIOLATION


async def test_unknown_organizations_get_a_visible_policy_close(app: FastAPI) -> None:
    async with connect(app, uuid.uuid4()) as socket:
        with pytest.raises(WebSocketClosedError) as closed:
            await socket.receive_json()
    assert closed.value.code == status.WS_1008_POLICY_VIOLATION
    assert closed.value.reason == "unknown organization"


async def test_non_browser_clients_need_no_origin(app: FastAPI, organization: Organization) -> None:
    async with connect(app, organization.id, origin=None) as socket:
        assert (await socket.receive_json())["type"] == "stream.ready"


async def test_malformed_organization_is_rejected(app: FastAPI) -> None:
    with pytest.raises(WebSocketClosedError) as closed:
        async with connect(app, "not-a-uuid"):
            pass
    assert closed.value.code == status.WS_1008_POLICY_VIOLATION


async def test_losing_redis_closes_the_stream_for_a_retry(
    app: FastAPI, organization: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def broken(*_: object, **__: object) -> None:
        await asyncio.sleep(0)
        raise RedisConnectionError("connection reset")

    async with connect(app, organization.id) as socket:
        await socket.receive_json()
        monkeypatch.setattr(PubSub, "get_message", broken)
        with pytest.raises(WebSocketClosedError) as closed:
            await socket.receive_event(wait=HEARTBEAT_SECONDS * 5)
    assert closed.value.code == status.WS_1011_INTERNAL_ERROR


class TestAuthenticatedStreams:
    @pytest.fixture
    def app_settings(self, redis_key_prefix: str) -> Settings:
        return Settings(
            env="test",
            auth_require_api_key=True,
            rate_limit_key_prefix=redis_key_prefix,
            events_key_prefix=redis_key_prefix,
            events_heartbeat_seconds=HEARTBEAT_SECONDS,
            _env_file=None,
        )

    @pytest.fixture
    async def api_key(self, session: AsyncSession, organization: Organization) -> str:
        secret = generate_api_key()
        await ApiKeyRepository(session).create(
            organization_id=organization.id, name="stream", key_hash=hash_api_key(secret)
        )
        return secret

    async def test_the_organization_parameter_alone_is_refused(
        self, app: FastAPI, organization: Organization
    ) -> None:
        async with connect(app, organization.id) as socket:
            with pytest.raises(WebSocketClosedError) as closed:
                await socket.receive_json()
        assert closed.value.code == status.WS_1008_POLICY_VIOLATION
        assert closed.value.reason.startswith("An API key is required")

    async def test_a_key_subprotocol_opens_the_stream_without_echoing_the_key(
        self, app: FastAPI, organization: Organization, api_key: str
    ) -> None:
        async with connect(app, None, subprotocols=bearer_protocols(api_key)) as socket:
            ready = await socket.receive_json()
            assert socket.subprotocol == "cortex.events.v1"
        assert ready["data"] == {"organization_id": str(organization.id)}

    async def test_an_authorization_header_opens_the_stream(
        self, app: FastAPI, organization: Organization, api_key: str
    ) -> None:
        headers = {"authorization": f"Bearer {api_key}"}
        async with connect(app, organization.id, origin=None, headers=headers) as socket:
            assert (await socket.receive_json())["type"] == "stream.ready"
            assert socket.subprotocol is None

    async def test_a_key_cannot_open_another_organizations_stream(
        self, app: FastAPI, api_key: str, organization_factory: OrganizationFactory
    ) -> None:
        other = await organization_factory()
        async with connect(app, other.id, subprotocols=bearer_protocols(api_key)) as socket:
            with pytest.raises(WebSocketClosedError) as closed:
                await socket.receive_json()
        assert closed.value.code == status.WS_1008_POLICY_VIOLATION
        assert closed.value.reason == "API key does not belong to the organization"

    async def test_unknown_keys_are_refused(self, app: FastAPI, organization: Organization) -> None:
        protocols = bearer_protocols("ctx_not-a-real-key")
        async with connect(app, organization.id, subprotocols=protocols) as socket:
            with pytest.raises(WebSocketClosedError) as closed:
                await socket.receive_json()
        assert closed.value.code == status.WS_1008_POLICY_VIOLATION
        assert closed.value.reason == "Invalid or revoked API key"

    async def test_expired_keys_are_refused(
        self, app: FastAPI, api_key: str, session: AsyncSession
    ) -> None:
        repository = ApiKeyRepository(session)
        key = await repository.get_by_hash(hash_api_key(api_key))
        assert key is not None
        key.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()
        async with connect(app, None, subprotocols=bearer_protocols(api_key)) as socket:
            with pytest.raises(WebSocketClosedError) as closed:
                await socket.receive_json()
        assert closed.value.reason == "API key has expired"

    async def test_foreign_origins_are_refused_even_with_a_key(
        self, app: FastAPI, api_key: str
    ) -> None:
        with pytest.raises(WebSocketClosedError) as closed:
            async with connect(
                app, None, "http://evil.test", subprotocols=bearer_protocols(api_key)
            ):
                pass
        assert closed.value.code == status.WS_1008_POLICY_VIOLATION
