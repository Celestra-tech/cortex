import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

import cortex_api.models  # noqa: F401  (registers every table on Base.metadata)
from cortex_api.core.config import get_settings
from cortex_api.database.base import Base
from cortex_api.database.config import DatabaseConfig
from cortex_api.database.session import create_engine

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

COMPARE_OPTIONS = {"compare_type": True}


def database_config() -> DatabaseConfig:
    """`sqlalchemy.url` in the Alembic config wins, so tests can target a scratch database."""
    base = DatabaseConfig.from_settings(get_settings())
    override = config.get_main_option("sqlalchemy.url")
    return base.model_copy(update={"url": DatabaseConfig(url=override).url}) if override else base


def run_migrations_offline() -> None:
    context.configure(
        url=str(database_config().url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMPARE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, **COMPARE_OPTIONS)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(database_config(), null_pool=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
