import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.services.memory.service import MemoryService
from cortex_api.services.memory.session import SessionCache


@pytest.fixture
def session_cache(redis: Redis, app_settings: Settings) -> SessionCache:
    return SessionCache.from_settings(redis, app_settings)


@pytest.fixture
def memory_service(session: AsyncSession, session_cache: SessionCache) -> MemoryService:
    return MemoryService(session, session_cache)
