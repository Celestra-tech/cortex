from typing import Any

import pytest

from cortex_api.core.config import Settings

SAFE_DEPLOYED: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://cortex:s3cret@localhost/cortex?host=/cloudsql/p:r:i",
    "api_cors_origins": "https://app.celestra.ai",
    "_env_file": None,
}


def test_development_defaults_are_convenient() -> None:
    settings = Settings(_env_file=None)
    assert settings.env == "development"
    assert settings.log_format == "console"
    assert settings.auth_require_api_key is False
    assert settings.is_deployed is False
    assert settings.metrics_port == 9464


def test_tests_do_not_bind_the_metrics_port() -> None:
    assert Settings(env="test", _env_file=None).metrics_port is None
    assert Settings(env="test", metrics_port=9999, _env_file=None).metrics_port == 9999


@pytest.mark.parametrize("env", ["staging", "production"])
def test_deployed_environments_get_secure_defaults(env: str) -> None:
    settings = Settings(env=env, **SAFE_DEPLOYED)
    assert settings.is_deployed
    assert settings.log_format == "json"
    assert settings.auth_require_api_key is True


def test_explicit_console_logs_are_allowed_when_deployed() -> None:
    assert Settings(env="staging", log_format="console", **SAFE_DEPLOYED).log_format == "console"


def test_production_lists_every_unsafe_setting_at_once() -> None:
    with pytest.raises(ValueError, match="Unsafe configuration") as caught:
        Settings(
            env="production",
            auth_require_api_key=False,
            api_cors_origins="*,http://localhost:3000",
            admin_token="short",  # noqa: S106 - deliberately weak
            _env_file=None,
        )
    message = str(caught.value)
    for problem in (
        "CORTEX_AUTH_REQUIRE_API_KEY must be true",
        "not '*'",
        "must not include local origins",
        "https:// origins",
        "CORTEX_DATABASE_URL must use a real password",
        "CORTEX_ADMIN_TOKEN must be at least 32 characters",
    ):
        assert problem in message


def test_staging_may_allow_local_dashboard_origins() -> None:
    settings = Settings(
        env="staging",
        **{
            **SAFE_DEPLOYED,
            "api_cors_origins": "https://staging.celestra.ai,http://localhost:3000",
        },
    )
    assert "http://localhost:3000" in settings.api_cors_origins


def test_the_request_limit_must_fit_the_largest_upload() -> None:
    with pytest.raises(ValueError, match="must exceed CORTEX_KNOWLEDGE_MAX_UPLOAD_BYTES"):
        Settings(api_max_request_bytes=4096, knowledge_max_upload_bytes=4096, _env_file=None)


def test_lists_parse_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_API_ALLOWED_HOSTS", "api.celestra.ai, *.run.app")
    assert Settings(_env_file=None).api_allowed_hosts == ["api.celestra.ai", "*.run.app"]


def test_gcp_project_accepts_the_standard_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "celestra-prod")
    assert Settings(_env_file=None).gcp_project_id == "celestra-prod"
