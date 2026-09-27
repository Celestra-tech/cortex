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

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_FULL = frozenset({Capability.CHAT, Capability.VISION, Capability.TOOLS, Capability.JSON})

OPENAI_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="gpt-4.1",
        provider=ProviderName.OPENAI,
        display_name="GPT-4.1",
        context_window=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=Decimal("2.00"),
        output_cost_per_mtok=Decimal("8.00"),
        quality=4,
        typical_latency_ms=2_500,
        capabilities=_FULL,
    ),
    ModelSpec(
        id="gpt-4.1-mini",
        provider=ProviderName.OPENAI,
        display_name="GPT-4.1 mini",
        context_window=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=Decimal("0.40"),
        output_cost_per_mtok=Decimal("1.60"),
        quality=3,
        typical_latency_ms=1_500,
        capabilities=_FULL,
    ),
    ModelSpec(
        id="o4-mini",
        provider=ProviderName.OPENAI,
        display_name="o4-mini",
        context_window=200_000,
        max_output_tokens=100_000,
        input_cost_per_mtok=Decimal("1.10"),
        output_cost_per_mtok=Decimal("4.40"),
        quality=4,
        typical_latency_ms=6_000,
        capabilities=_FULL | {Capability.REASONING},
        supports_temperature=False,
    ),
)

_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
    "tool_calls": FinishReason.TOOL_CALLS,
    "function_call": FinishReason.TOOL_CALLS,
}


class OpenAIProvider(HTTPProvider):
    """OpenAI Chat Completions, and any host that speaks the same protocol.

    DeepSeek, Qwen (DashScope compatible mode), and Llama hosts (vLLM, Groq,
    Together, Ollama) expose this API. Adding one is a registration, not new
    code: pass its `ProviderName`, base URL, model catalog, and
    `max_tokens_field="max_tokens"`.
    """

    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
        models: Sequence[ModelSpec] = OPENAI_MODELS,
        name: ProviderName = ProviderName.OPENAI,
        max_tokens_field: str = "max_completion_tokens",
    ) -> None:
        super().__init__(name, models, client=client, config=config)
        self.max_tokens_field = max_tokens_field

    def build_payload(self, request: ProviderRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.extend(
            {"role": str(role), "content": content} for role, content in request.turns()
        )

        payload: dict[str, Any] = {
            "model": request.model.id,
            "messages": messages,
            self.max_tokens_field: request.max_tokens,
        }
        if request.temperature is not None and request.model.supports_temperature:
            payload["temperature"] = request.temperature
        return payload

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = await self.post_json(
            "/chat/completions",
            self.build_payload(request),
            {"Authorization": f"Bearer {self.api_key}"},
        )
        return self.parse_response(body, request)

    def parse_response(self, body: dict[str, Any], request: ProviderRequest) -> ProviderResponse:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "response has no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        output = message.get("content") or message.get("refusal") or ""
        finish = _FINISH_REASONS.get(str(choice.get("finish_reason")), FinishReason.OTHER)
        if message.get("refusal") and not message.get("content"):
            finish = FinishReason.CONTENT_FILTER

        usage = body.get("usage") or {}
        return ProviderResponse(
            output=str(output),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=finish,
            provider_model=str(body.get("model") or request.model.id),
            provider_request_id=body.get("id"),
        )
