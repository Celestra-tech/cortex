"""Test doubles for the router: scriptable providers, a manual clock, a sleep recorder."""

from collections.abc import Sequence

from cortex_api.services.router.base import (
    FinishReason,
    ModelSpec,
    Provider,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    ProviderRequest,
    ProviderResponse,
)
from cortex_api.services.router.providers.anthropic_provider import ANTHROPIC_MODELS
from cortex_api.services.router.providers.gemini_provider import GEMINI_MODELS
from cortex_api.services.router.providers.openai_provider import OPENAI_MODELS

CATALOGS: dict[ProviderName, Sequence[ModelSpec]] = {
    ProviderName.OPENAI: OPENAI_MODELS,
    ProviderName.ANTHROPIC: ANTHROPIC_MODELS,
    ProviderName.GEMINI: GEMINI_MODELS,
}


class FakeProvider(Provider):
    """Scriptable provider: queued outcomes are consumed in order, then it succeeds."""

    def __init__(
        self, name: ProviderName, models: Sequence[ModelSpec], *, configured: bool = True
    ) -> None:
        super().__init__(name, models)
        self.is_configured = configured
        self.script: list[ProviderResponse | ProviderError | BaseException] = []
        self.calls: list[ProviderRequest] = []

    @property
    def configured(self) -> bool:
        return self.is_configured

    def fail(
        self,
        kind: ProviderErrorKind = ProviderErrorKind.SERVER,
        *,
        times: int = 1,
        message: str = "upstream exploded",
        retry_after_seconds: float | None = None,
    ) -> None:
        status = {
            ProviderErrorKind.SERVER: 500,
            ProviderErrorKind.RATE_LIMITED: 429,
            ProviderErrorKind.AUTHENTICATION: 401,
            ProviderErrorKind.BAD_REQUEST: 400,
        }.get(kind)
        for _ in range(times):
            self.script.append(
                ProviderError(
                    self.name,
                    kind,
                    message,
                    status_code=status,
                    retry_after_seconds=retry_after_seconds,
                )
            )

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome or ProviderResponse(
            output=f"{self.name} answered with {request.model.id}",
            prompt_tokens=120,
            completion_tokens=30,
            finish_reason=FinishReason.STOP,
            provider_model=f"{request.model.id}-2026-01-01",
            provider_request_id=f"{self.name}-req",
        )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SleepRecorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
