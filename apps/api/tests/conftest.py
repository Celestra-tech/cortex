import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from cortex_api.api.deps import ORGANIZATION_HEADER, get_embedder, get_router
from cortex_api.cache.redis import get_redis
from cortex_api.core.config import Settings
from cortex_api.database.config import DatabaseConfig
from cortex_api.database.session import create_engine, get_db_session
from cortex_api.main import create_app
from cortex_api.models.organization import Organization
from cortex_api.repositories.organization import OrganizationRepository
from cortex_api.services.knowledge.embeddings import Embedder, HashingEmbeddingProvider
from cortex_api.services.router.router import CortexRouter

OrganizationFactory = Callable[..., Awaitable[Organization]]

API_DIR = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL_ENV = "CORTEX_TEST_DATABASE_URL"
TEST_REDIS_URL_ENV = "CORTEX_TEST_REDIS_URL"

_GATES = {"database": TEST_DATABASE_URL_ENV, "redis": TEST_REDIS_URL_ENV}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, env_var in _GATES.items():
        if os.environ.get(env_var):
            continue
        skip = pytest.mark.skip(reason=f"{env_var} is not set")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def settings() -> Settings:
    return Settings(env="test", _env_file=None)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# --- PostgreSQL --------------------------------------------------------------


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    database = make_url(url).database or ""
    # Migration tests drop every table; refuse to run against anything but a scratch database.
    if not database.endswith("_test"):
        pytest.fail(
            f"{TEST_DATABASE_URL_ENV} must name a database ending in '_test', got {database!r}"
        )
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_database(alembic_config: Config, database_url: str) -> str:
    command.upgrade(alembic_config, "head")
    return database_url


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(DatabaseConfig(url=migrated_database), null_pool=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Session inside an outer transaction that is always rolled back.

    Commits made by code under test become savepoint releases, so every test
    starts from an empty, migrated schema.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


# --- Redis -------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_url() -> str:
    url = os.environ.get(TEST_REDIS_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_REDIS_URL_ENV} is not set")
    # Tests only ever delete their own prefixed keys, but keep them out of the default DB anyway.
    if urlsplit(url).path.strip("/") in {"", "0"}:
        pytest.fail(f"{TEST_REDIS_URL_ENV} must select a non-default database, e.g. /15")
    return url


@pytest.fixture
def redis_key_prefix() -> str:
    return f"cortex-test:{uuid.uuid4().hex}:"


@pytest.fixture
async def redis(redis_url: str, redis_key_prefix: str) -> AsyncIterator[Redis]:
    client = Redis.from_url(redis_url, decode_responses=True)
    try:
        yield client
    finally:
        # Never FLUSHDB: the server may be shared. Remove only this test's keys.
        keys = [key async for key in client.scan_iter(match=f"{redis_key_prefix}*", count=500)]
        if keys:
            await client.delete(*keys)
        await client.aclose()


# --- Tenants and in-process API ----------------------------------------------------


@pytest.fixture
def organization_factory(session: AsyncSession) -> OrganizationFactory:
    async def create(settings: dict[str, Any] | None = None) -> Organization:
        suffix = uuid.uuid4().hex[:12]
        return await OrganizationRepository(session).create(
            name=f"Org {suffix}", slug=f"org-{suffix}", settings=settings or {}
        )

    return create


@pytest.fixture
async def organization(organization_factory: OrganizationFactory) -> Organization:
    return await organization_factory()


@pytest.fixture
def headers(organization: Organization) -> dict[str, str]:
    return {ORGANIZATION_HEADER: str(organization.id)}


@pytest.fixture
def app_settings(redis_key_prefix: str) -> Settings:
    return Settings(
        env="test",
        memory_session_key_prefix=redis_key_prefix,
        knowledge_cache_key_prefix=redis_key_prefix,
        rate_limit_key_prefix=redis_key_prefix,
        events_key_prefix=redis_key_prefix,
        _env_file=None,
    )


@pytest.fixture
def router_override() -> CortexRouter | None:
    """Router suites override this to plug fake providers into `api`."""
    return None


@pytest.fixture
def embedder() -> Embedder:
    """Offline, deterministic embeddings. Knowledge suites may override this."""
    return Embedder(HashingEmbeddingProvider())


@pytest.fixture
def app(
    session: AsyncSession,
    redis: Redis,
    app_settings: Settings,
    router_override: CortexRouter | None,
    embedder: Embedder,
) -> FastAPI:
    """The API wired to the test's rolled-back session and prefixed Redis keys."""
    app = create_app(app_settings)
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_embedder] = lambda: embedder
    if router_override is not None:
        app.dependency_overrides[get_router] = lambda: router_override
    return app


@pytest.fixture
async def api(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """In-process HTTP client for `app`."""
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://cortex.test") as client:
        yield client
