from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import (
    AliasChoices,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["console", "json"]

DEPLOYED_ENVIRONMENTS: frozenset[Environment] = frozenset({"staging", "production"})
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")  # noqa: S104 - matched, not bound


class ConfigurationError(ValueError):
    """Settings that are unsafe for the selected environment."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CORTEX_",
        # Later files take priority: a local apps/api/.env overrides the repo root .env.
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        # Blank values fall through, so `CORTEX_OPENAI_API_KEY=` never masks OPENAI_API_KEY.
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    # Staging and production switch to secure defaults (JSON logs, required API keys) and
    # refuse to start with unsafe explicit values; see `_apply_environment_profile`.
    env: Environment = "development"
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"
    release: str | None = None
    """Deployed version, e.g. `1.0.0-alpha`; reported in logs, traces, and metrics."""
    revision: str | None = None
    """Source revision (git SHA) of the running build."""

    api_title: str = "CELESTRA Cortex API"
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    # Host header allowlist; `*` accepts any. Cloud Run already routes by host.
    api_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    # Largest accepted request body. Uploads are capped separately by knowledge_max_upload_bytes.
    api_max_request_bytes: int = Field(default=30 * 1024 * 1024, ge=1024)
    # Strict-Transport-Security max-age, sent in staging and production only.
    security_hsts_seconds: int = Field(default=63_072_000, ge=0)

    # When false, requests without an API key may name their tenant via X-Organization-ID.
    # Development only: staging and production require keys.
    auth_require_api_key: bool = False
    # last_used_at is written at most this often per key, keeping auth off the write path.
    auth_last_used_interval_seconds: int = Field(default=60, ge=0)
    # Enables POST /v1/admin/organizations (bootstrap a tenant and its first admin key).
    # Unset disables the endpoint entirely.
    admin_token: SecretStr | None = None

    # Per API key (or per organization without one), fixed one-minute windows in Redis.
    # organization.settings["rate_limit_per_minute"] overrides the default per tenant.
    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=600, ge=1)
    rate_limit_key_prefix: str = ""

    # Prometheus metrics on a separate port, so they are never exposed on the public
    # ingress. None disables the listener (the metrics are still collected).
    metrics_enabled: bool = True
    metrics_port: int | None = Field(default=9464, ge=1, le=65535)

    # OpenTelemetry tracing, exported over OTLP/HTTP. The standard OTEL_EXPORTER_OTLP_*
    # variables configure headers and protocol details.
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "cortex-api"
    otel_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    # Links JSON log records to Cloud Trace. Cloud Run does not set this automatically.
    gcp_project_id: str | None = Field(
        default=None, validation_alias=AliasChoices("CORTEX_GCP_PROJECT_ID", "GOOGLE_CLOUD_PROJECT")
    )

    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://cortex:cortex@localhost:5432/celestra_cortex"
    )
    database_pool_size: int = Field(default=5, ge=1)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=-1)
    database_statement_timeout_ms: int = Field(default=30_000, ge=0)
    database_echo: bool = False

    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    memory_session_ttl_seconds: int = Field(default=86_400, ge=60)
    memory_session_max_messages: int = Field(default=50, ge=1, le=1000)
    memory_session_max_tokens: int = Field(default=8_000, ge=1)
    memory_session_key_prefix: str = ""
    memory_retrieval_half_life_days: float = Field(default=30.0, gt=0)

    health_check_timeout_seconds: float = Field(default=2.0, gt=0)

    # Provider credentials also accept each vendor's conventional variable name. The first
    # alias present wins, so the CORTEX_ name must stay first.
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CORTEX_OPENAI_API_KEY", "OPENAI_API_KEY"),
    )
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CORTEX_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CORTEX_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    router_timeout_seconds: float = Field(default=60.0, gt=0)
    router_max_retries: int = Field(default=1, ge=0, le=5)
    router_fallback_order: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["openai", "anthropic", "gemini"]
    )
    router_default_max_tokens: int = Field(default=1024, ge=1)
    router_circuit_failure_threshold: int = Field(default=3, ge=1)
    router_circuit_cooldown_seconds: float = Field(default=30.0, ge=0)

    # "local" is a deterministic hashing embedder: offline and free, but lexical rather than
    # semantic. Production deployments should select openai or gemini.
    knowledge_embedding_provider: Literal["local", "openai", "gemini"] = "local"
    knowledge_embedding_model: str | None = None
    knowledge_embedding_dimensions: int = Field(default=1536, ge=8, le=1536)
    # Points the openai provider at any OpenAI-compatible server (vLLM, Ollama, TEI).
    knowledge_embedding_base_url: str | None = None
    knowledge_embedding_batch_size: int = Field(default=64, ge=1, le=2048)
    knowledge_embedding_max_concurrency: int = Field(default=4, ge=1, le=32)
    knowledge_embedding_max_retries: int = Field(default=2, ge=0, le=5)
    knowledge_chunk_size: int = Field(default=512, ge=32, le=8192)
    knowledge_chunk_overlap: int = Field(default=64, ge=0, le=2048)
    knowledge_chunk_separators: list[str] | None = None
    knowledge_max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    knowledge_max_document_chars: int = Field(default=5_000_000, ge=1000)
    knowledge_max_chunks_per_document: int = Field(default=10_000, ge=1)
    knowledge_top_k: int = Field(default=8, ge=1, le=50)
    knowledge_rrf_k: int = Field(default=60, ge=1)
    knowledge_vector_weight: float = Field(default=1.0, ge=0)
    knowledge_keyword_weight: float = Field(default=1.0, ge=0)
    knowledge_recency_weight: float = Field(default=0.1, ge=0, le=1)
    knowledge_recency_half_life_days: float = Field(default=180.0, gt=0)
    knowledge_max_context_tokens: int = Field(default=3000, ge=100, le=200_000)
    knowledge_query_cache_ttl_seconds: int = Field(default=86_400, ge=0)
    knowledge_cache_key_prefix: str = ""

    # Real-time events fan out over Redis pub/sub on `{prefix}events:{organization_id}`.
    events_key_prefix: str = ""
    events_heartbeat_seconds: float = Field(default=20.0, gt=0, le=300)

    @field_validator(
        "api_cors_origins", "api_allowed_hosts", "router_fallback_order", mode="before"
    )
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _apply_environment_profile(self) -> Self:
        """Secure defaults for deployed environments, then reject unsafe explicit values."""
        if self.api_max_request_bytes <= self.knowledge_max_upload_bytes:
            raise ConfigurationError(
                "CORTEX_API_MAX_REQUEST_BYTES must exceed CORTEX_KNOWLEDGE_MAX_UPLOAD_BYTES "
                "(multipart framing adds overhead to every upload)"
            )
        explicit = self.model_fields_set
        if self.env == "test" and "metrics_port" not in explicit:
            self.metrics_port = None  # parallel test apps must not contend for the port
        if self.env not in DEPLOYED_ENVIRONMENTS:
            return self
        if "log_format" not in explicit:
            self.log_format = "json"
        if "auth_require_api_key" not in explicit:
            self.auth_require_api_key = True

        problems: list[str] = []
        if not self.auth_require_api_key:
            problems.append("CORTEX_AUTH_REQUIRE_API_KEY must be true")
        if "*" in self.api_cors_origins:
            problems.append("CORTEX_API_CORS_ORIGINS must list origins explicitly, not '*'")
        if self.env == "production":
            local = [o for o in self.api_cors_origins if any(h in o for h in _LOCAL_HOSTS)]
            if local:
                problems.append(f"CORTEX_API_CORS_ORIGINS must not include local origins: {local}")
            if not self.api_cors_origins or any(
                not o.startswith("https://") for o in self.api_cors_origins
            ):
                problems.append("CORTEX_API_CORS_ORIGINS must be https:// origins in production")
        database = self.database_url.hosts()[0]
        if database.get("password") in (None, "", "cortex"):
            problems.append("CORTEX_DATABASE_URL must use a real password, not the default")
        if self.admin_token is not None and len(self.admin_token.get_secret_value()) < 32:
            problems.append("CORTEX_ADMIN_TOKEN must be at least 32 characters")
        if problems:
            raise ConfigurationError(
                f"Unsafe configuration for CORTEX_ENV={self.env}:\n- " + "\n- ".join(problems)
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_deployed(self) -> bool:
        """Staging or production: public, multi-instance, secured."""
        return self.env in DEPLOYED_ENVIRONMENTS


@lru_cache
def get_settings() -> Settings:
    return Settings()
