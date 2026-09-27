from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import httpx2

from cortex_api.services.router.base import (
    Capability,
    FinishReason,
    HTTPProvider,
    HTTPProviderConfig,
    ModelSpec,
    ProviderErrorKind,
    ProviderName,
    ProviderRequest,
    ProviderResponse,
)

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
MAX_TEMPERATURE = 1.0

_CAPABILITIES = frozenset(
    {Capability.CHAT, Capability.VISION, Capability.TOOLS, Capability.REASONING}
)

ANTHROPIC_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="claude-sonnet-4-5",
        provider=ProviderName.ANTHROPIC,
        display_name="Claude Sonnet 4.5",
        context_window=200_000,
        max_output_tokens=64_000,
        input_cost_per_mtok=Decimal("3.00"),
        output_cost_per_mtok=Decimal("15.00"),
        quality=5,
        typical_latency_ms=3_000,
        capabilities=_CAPABILITIES,
    ),
    ModelSpec(
        id="claude-haiku-4-5",
        provider=ProviderName.ANTHROPIC,
        display_name="Claude Haiku 4.5",
        context_window=200_000,
        max_output_tokens=64_000,
        input_cost_per_mtok=Decimal("1.00"),
        output_cost_per_mtok=Decimal("5.00"),
        quality=3,
        typical_latency_ms=1_200,
        capabilities=_CAPABILITIES,
    ),
    ModelSpec(
        id="claude-opus-4-1",
        provider=ProviderName.ANTHROPIC,
        display_name="Claude Opus 4.1",
        context_window=200_000,
        max_output_tokens=32_000,
        input_cost_per_mtok=Decimal("15.00"),
        output_cost_per_mtok=Decimal("75.00"),
        quality=5,
        typical_latency_ms=5_000,
        capabilities=_CAPABILITIES,
    ),
)

_FINISH_REASONS = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "model_context_window_exceeded": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_CALLS,
    "refusal": FinishReason.CONTENT_FILTER,
}


class AnthropicProvider(HTTPProvider):
    """Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
        models: Sequence[ModelSpec] = ANTHROPIC_MODELS,
    ) -> None:
        super().__init__(ProviderName.ANTHROPIC, models, client=client, config=config)

    def build_payload(self, request: ProviderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model.id,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": str(role), "content": content} for role, content in request.turns()
            ],
        }
        if request.system_prompt:
            payload["system"] = request.system_prompt
        if request.temperature is not None and request.model.supports_temperature:
            payload["temperature"] = min(request.temperature, MAX_TEMPERATURE)
        return payload

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = await self.post_json(
            "/messages",
            self.build_payload(request),
            {"x-api-key": self.api_key, "anthropic-version": API_VERSION},
        )
        return self.parse_response(body, request)

    def parse_response(self, body: dict[str, Any], request: ProviderRequest) -> ProviderResponse:
        content = body.get("content")
        if not isinstance(content, list):
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "response has no content")
        output = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = body.get("usage") or {}
        return ProviderResponse(
            output=output,
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=_FINISH_REASONS.get(str(body.get("stop_reason")), FinishReason.OTHER),
            provider_model=str(body.get("model") or request.model.id),
            provider_request_id=body.get("id"),
        )
