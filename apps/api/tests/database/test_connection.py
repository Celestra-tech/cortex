import pytest
from fastapi.testclient import TestClient
from pydantic import PostgresDsn
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from cortex_api.core.config import Settings
from cortex_api.main import create_app


@pytest.mark.database
async def test_engine_connects_with_session_settings(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        version: str = (await connection.execute(text("SELECT version()"))).scalar_one()
        timezone: str = (await connection.execute(text("SHOW timezone"))).scalar_one()
        application: str = (await connection.execute(text("SHOW application_name"))).scalar_one()

    assert version.startswith("PostgreSQL")
    assert timezone == "UTC"
    assert application == "cortex-api"


@pytest.mark.database
def test_database_health_reports_connected(migrated_database: str) -> None:
    settings = Settings(env="test", database_url=PostgresDsn(migrated_database), _env_file=None)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/database")

    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "connected"}


def test_database_health_reports_disconnected() -> None:
    settings = Settings(
        env="test",
        database_url=PostgresDsn("postgresql+asyncpg://cortex:cortex@127.0.0.1:1/unreachable"),
        health_check_timeout_seconds=1.0,
        _env_file=None,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/database")

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "disconnected"}
