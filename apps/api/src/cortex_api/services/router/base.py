"""Provider-neutral contracts. Nothing outside `providers/` sees a vendor format."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

import httpx2

from cortex_api.models.message import MessageRole

_MTOK = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.00000001")
_MAX_ERROR_CHARS = 500


class ProviderName(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    # Reserved for OpenAI-compatible hosts; see `providers.openai_provider`.
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    LLAMA = "llama"


class Capability(StrEnum):
    CHAT = "chat"
    VISION = "vision"
    TOOLS = "tools"
    JSON = "json_mode"
    REASONING = "reasoning"


class FinishReason(StrEnum):
    STOP = "stop"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    TOOL_CALLS = "tool_calls"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Static facts about one model. Prices are USD per million tokens (estimates)."""

    id: str
    provider: ProviderName
    display_name: str
    context_window: int
    max_output_tokens: int
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal
    quality: int
    """Relative capability, 1 (basic) to 5 (frontier). Used only for ranking."""
    typical_latency_ms: int
    """Prior for a short completion, replaced by observed latency once traffic flows."""
    capabilities: frozenset[Capability] = frozenset({Capability.CHAT})
    supports_temperature: bool = True
    aliases: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.id}"

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        cost = (
            Decimal(prompt_tokens) * self.input_cost_per_mtok
            + Decimal(completion_tokens) * self.output_cost_per_mtok
        ) / _MTOK
        return cost.quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    model: ModelSpec
    messages: tuple[ChatMessage, ...]
    max_tokens: int
    temperature: float | None = None

    @property
    def system_prompt(self) -> str | None:
        parts = [m.content for m in self.messages if m.role is MessageRole.SYSTEM]
        return "\n\n".join(parts) if parts else None

    def turns(self) -> list[tuple[MessageRole, str]]:
        """Non-system messages as alternating user/assistant turns.

        Tool results are folded into user turns (tool calling is not routed
        yet) and consecutive same-role turns are merged, which every provider
        accepts.
        """
        turns: list[tuple[MessageRole, str]] = []
        for message in self.messages:
            if message.role is MessageRole.SYSTEM:
                continue
            role, content = message.role, message.content
            if role is MessageRole.TOOL:
                role, content = MessageRole.USER, f"Tool result:\n{content}"
            if turns and turns[-1][0] is role:
                turns[-1] = (role, f"{turns[-1][1]}\n\n{content}")
            else:
                turns.append((role, content))
        return turns


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    output: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: FinishReason
    provider_model: str
    """Exact model version reported by the provider."""
    provider_request_id: str | None = None


class ProviderErrorKind(StrEnum):
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    SERVER = "server_error"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"


RETRYABLE_ERRORS = frozenset(
    {
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.CONNECTION,
        ProviderErrorKind.SERVER,
    }
)
"""Worth retrying against the same provider."""

HEALTH_ERRORS = RETRYABLE_ERRORS | {
    ProviderErrorKind.AUTHENTICATION,
    ProviderErrorKind.INVALID_RESPONSE,
}
"""Count against a provider's circuit breaker. Bad requests are the caller's fault."""


class ProviderError(Exception):
    def __init__(
        self,
        provider: str,
        kind: ProviderErrorKind,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.provider = provider
        self.kind = kind
        self.message = message[:_MAX_ERROR_CHARS]
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{provider} {kind}: {self.message}")

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_ERRORS

    def describe(self) -> str:
        status = f" (HTTP {self.status_code})" if self.status_code else ""
        return f"{self.kind}{status}: {self.message}"


class Provider(ABC):
    """One upstream API. Implementations translate to and from the neutral types."""

    def __init__(self, name: ProviderName, models: Sequence[ModelSpec]) -> None:
        mismatched = [spec.id for spec in models if spec.provider is not name]
        if mismatched:
            raise ValueError(f"{name} provider given foreign models: {mismatched}")
        self.name = name
        self.models: tuple[ModelSpec, ...] = tuple(models)

    @property
    @abstractmethod
    def configured(self) -> bool:
        """Whether credentials are present. Unconfigured providers are never routed to."""

    @abstractmethod
    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...


@dataclass(slots=True)
class HTTPProviderConfig:
    api_key: str | None
    base_url: str
    timeout_seconds: float = 60.0
    extra_headers: Mapping[str, str] = field(default_factory=dict)


class JSONClient:
    """Transport and error mapping for a JSON-over-HTTPS upstream API.

    Shared by chat providers and embedding providers so every upstream failure
    is classified the same way.
    """

    def __init__(
        self, upstream: str, client: httpx2.AsyncClient, config: HTTPProviderConfig
    ) -> None:
        self.upstream = upstream
        self.client = client
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key)

    @property
    def api_key(self) -> str:
        if not self.config.api_key:
            raise self.error(ProviderErrorKind.UNAVAILABLE, "no API key configured")
        return self.config.api_key

    def error(self, kind: ProviderErrorKind, message: str, **kwargs: Any) -> ProviderError:
        return ProviderError(self.upstream, kind, message, **kwargs)

    async def post_json(
        self, path: str, payload: Mapping[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any]:
        try:
            response = await self.client.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={**self.config.extra_headers, **headers},
                timeout=self.config.timeout_seconds,
            )
        except httpx2.TimeoutException as exc:
            raise self.error(ProviderErrorKind.TIMEOUT, f"request timed out: {exc!r}") from exc
        except httpx2.TransportError as exc:
            raise self.error(ProviderErrorKind.CONNECTION, f"transport error: {exc!r}") from exc

        if response.status_code >= 400:
            raise self._status_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise self.error(
                ProviderErrorKind.INVALID_RESPONSE, "response body is not JSON"
            ) from exc
        if not isinstance(body, dict):
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "response body is not an object")
        return body

    def _status_error(self, response: httpx2.Response) -> ProviderError:
        status = response.status_code
        if status in (401, 403):
            kind = ProviderErrorKind.AUTHENTICATION
        elif status == 408:
            kind = ProviderErrorKind.TIMEOUT
        elif status == 429:
            kind = ProviderErrorKind.RATE_LIMITED
        elif status >= 500:
            kind = ProviderErrorKind.SERVER
        else:
            kind = ProviderErrorKind.BAD_REQUEST
        return self.error(
            kind,
            _error_message(response),
            status_code=status,
            retry_after_seconds=_retry_after(response),
        )


class HTTPProvider(JSONClient, Provider):
    """A chat provider spoken to over JSON-over-HTTPS."""

    def __init__(
        self,
        name: ProviderName,
        models: Sequence[ModelSpec],
        *,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
    ) -> None:
        Provider.__init__(self, name, models)
        JSONClient.__init__(self, name, client, config)


def _error_message(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:_MAX_ERROR_CHARS] or response.reason_phrase
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return str(error["message"])
    if isinstance(error, str):
        return error
    return response.reason_phrase or f"HTTP {response.status_code}"


def _retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
