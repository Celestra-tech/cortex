from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["healthy"] = "healthy"
    service: str
    version: str


class DatabaseHealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    database: Literal["connected", "disconnected"]


class DependencyCheck(BaseModel):
    status: Literal["up", "down"]
    latency_ms: float | None = Field(default=None, description="Round-trip time of the probe.")
    error: str | None = Field(default=None, description="Exception class name when down.")


class DependencyChecks(BaseModel):
    postgres: DependencyCheck
    redis: DependencyCheck


class ReadinessResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    service: str
    version: str
    checks: DependencyChecks


class RedisHealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    redis: Literal["connected", "disconnected"]
    latency_ms: float | None = None


class ProviderHealth(BaseModel):
    name: str
    status: Literal["available", "circuit_open", "unconfigured"]
    configured: bool
    circuit_open: bool
    consecutive_failures: int
    last_error: str | None


class EmbeddingHealth(BaseModel):
    space: str
    configured: bool


class ProvidersHealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"] = Field(
        description="unhealthy: no chat provider can take traffic; degraded: some cannot."
    )
    available: int
    providers: list[ProviderHealth]
    embeddings: EmbeddingHealth
