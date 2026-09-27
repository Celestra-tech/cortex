import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

import cortex_api.models  # noqa: F401
from cortex_api.database.base import Base
from cortex_api.database.config import DatabaseConfig
from cortex_api.database.session import create_engine

pytestmark = pytest.mark.database

CORE_TABLES = {"organizations", "users", "api_keys", "audit_logs"}


def _schema_drift(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return set(await connection.run_sync(lambda c: inspect(c).get_table_names()))


async def test_migrations_round_trip_and_match_models(
    alembic_config: Config, database_url: str
) -> None:
    assert len(ScriptDirectory.from_config(alembic_config).get_heads()) == 1

    engine = create_engine(DatabaseConfig(url=database_url), null_pool=True)
    try:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
        assert not (await _table_names(engine) & CORE_TABLES)

        await asyncio.to_thread(command.upgrade, alembic_config, "head")
        assert await _table_names(engine) >= CORE_TABLES

        async with engine.connect() as connection:
            drift = await connection.run_sync(_schema_drift)
        assert drift == [], f"models and migrations disagree: {drift}"

        # What `alembic check` runs: autogenerate against the live schema must find nothing.
        await asyncio.to_thread(command.check, alembic_config)
    finally:
        await engine.dispose()
