from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.requests import HTTPConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from cortex_api.database.config import DatabaseConfig


def create_engine(config: DatabaseConfig, *, null_pool: bool = False) -> AsyncEngine:
    """Build the process-wide async engine.

    `null_pool` opens a fresh connection per checkout; use it for migrations,
    tests, and short-lived scripts where pooling only adds cross-event-loop hazards.
    """
    pool_options: dict[str, Any] = (
        {"poolclass": NullPool}
        if null_pool
        else {
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow,
            "pool_timeout": config.pool_timeout_seconds,
            "pool_recycle": config.pool_recycle_seconds,
            "pool_pre_ping": True,
        }
    )
    return create_async_engine(
        str(config.url),
        echo=config.echo,
        connect_args={
            "server_settings": {
                "application_name": config.application_name,
                "statement_timeout": str(config.statement_timeout_ms),
                "timezone": "UTC",
            },
        },
        **pool_options,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.db_engine
    return engine


async def get_db_session(request: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """Request-scoped session.

    Callers own the transaction boundary and must `await session.commit()`;
    anything uncommitted is rolled back when the request ends.
    """
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db_session)]
