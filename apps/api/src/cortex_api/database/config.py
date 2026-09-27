from typing import Self

from pydantic import BaseModel, ConfigDict, Field, PostgresDsn, field_validator

from cortex_api import SERVICE_NAME
from cortex_api.core.config import Settings

ASYNC_DRIVER_SCHEME = "postgresql+asyncpg"
_SYNC_SCHEMES = ("postgres://", "postgresql://", "postgresql+psycopg://", "postgresql+psycopg2://")


class DatabaseConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: PostgresDsn
    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout_seconds: float = Field(default=30.0, gt=0)
    pool_recycle_seconds: int = Field(default=1800, ge=-1)
    statement_timeout_ms: int = Field(default=30_000, ge=0)
    echo: bool = False
    application_name: str = SERVICE_NAME

    @field_validator("url", mode="before")
    @classmethod
    def _force_async_driver(cls, value: object) -> object:
        # Managed providers hand out postgres:// URLs; the app only speaks asyncpg.
        raw = str(value)
        for scheme in _SYNC_SCHEMES:
            if raw.startswith(scheme):
                return f"{ASYNC_DRIVER_SCHEME}://{raw.removeprefix(scheme)}"
        return value

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            url=settings.database_url,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout_seconds=settings.database_pool_timeout_seconds,
            pool_recycle_seconds=settings.database_pool_recycle_seconds,
            statement_timeout_ms=settings.database_statement_timeout_ms,
            echo=settings.database_echo,
        )
