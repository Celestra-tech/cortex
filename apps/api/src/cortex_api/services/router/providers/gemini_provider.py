from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import httpx2

from cortex_api.models.message import MessageRole
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

_CAPABILITIES = frozenset(
    {Capability.CHAT, Capability.VISION, Capability.TOOLS, Capability.JSON, Capability.REASONING}
)

GEMINI_MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        id="gemini-2.5-pro",
        provider=ProviderName.GEMINI,
        display_name="Gemini 2.5 Pro",
        context_window=1_048_576,
        max_output_tokens=65_536,
        input_cost_per_mtok=Decimal("1.25"),
        output_cost_per_mtok=Decimal("10.00"),
        quality=5,
        typical_latency_ms=4_000,
        capabilities=_CAPABILITIES,
    ),
    ModelSpec(
        id="gemini-2.5-flash",
        provider=ProviderName.GEMINI,
        display_name="Gemini 2.5 Flash",
        context_window=1_048_576,
        max_output_tokens=65_536,
        input_cost_per_mtok=Decimal("0.30"),
        output_cost_per_mtok=Decimal("2.50"),
        quality=3,
        typical_latency_ms=1_200,
        capabilities=_CAPABILITIES,
    ),
    ModelSpec(
        id="gemini-2.5-flash-lite",
        provider=ProviderName.GEMINI,
        display_name="Gemini 2.5 Flash-Lite",
        context_window=1_048_576,
        max_output_tokens=65_536,
        input_cost_per_mtok=Decimal("0.10"),
        output_cost_per_mtok=Decimal("0.40"),
        quality=2,
        typical_latency_ms=800,
        capabilities=_CAPABILITIES,
    ),
)

_FINISH_REASONS = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.LENGTH,
    "SAFETY": FinishReason.CONTENT_FILTER,
    "RECITATION": FinishReason.CONTENT_FILTER,
    "BLOCKLIST": FinishReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": FinishReason.CONTENT_FILTER,
    "SPII": FinishReason.CONTENT_FILTER,
    "IMAGE_SAFETY": FinishReason.CONTENT_FILTER,
    "MALFORMED_FUNCTION_CALL": FinishReason.TOOL_CALLS,
}


class GeminiProvider(HTTPProvider):
    """Google Gemini API (`generateContent`)."""

    def __init__(
        self,
        *,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
        models: Sequence[ModelSpec] = GEMINI_MODELS,
    ) -> None:
        super().__init__(ProviderName.GEMINI, models, client=client, config=config)

    def build_payload(self, request: ProviderRequest) -> dict[str, Any]:
        generation: dict[str, Any] = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None and request.model.supports_temperature:
            generation["temperature"] = request.temperature

        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if role is MessageRole.ASSISTANT else "user",
                    "parts": [{"text": content}],
                }
                for role, content in request.turns()
            ],
            "generationConfig": generation,
        }
        if request.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": request.system_prompt}]}
        return payload

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        body = await self.post_json(
            f"/models/{request.model.id}:generateContent",
            self.build_payload(request),
            {"x-goog-api-key": self.api_key},
        )
        return self.parse_response(body, request)

    def parse_response(self, body: dict[str, Any], request: ProviderRequest) -> ProviderResponse:
        usage = body.get("usageMetadata") or {}
        prompt_tokens = int(usage.get("promptTokenCount") or 0)
        # Thinking tokens are billed as output.
        completion_tokens = int(usage.get("candidatesTokenCount") or 0) + int(
            usage.get("thoughtsTokenCount") or 0
        )
        provider_model = str(body.get("modelVersion") or request.model.id)
        request_id = body.get("responseId")

        candidates = body.get("candidates")
        if not candidates:
            feedback = body.get("promptFeedback") or {}
            if feedback.get("blockReason"):
                return ProviderResponse(
                    output="",
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    finish_reason=FinishReason.CONTENT_FILTER,
                    provider_model=provider_model,
                    provider_request_id=request_id,
                )
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "response has no candidates")

        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        parts = (candidate.get("content") or {}).get("parts") or []
        output = "".join(
            str(part.get("text", ""))
            for part in parts
            if isinstance(part, dict) and not part.get("thought")
        )
        return ProviderResponse(
            output=output,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=_FINISH_REASONS.get(
                str(candidate.get("finishReason")), FinishReason.OTHER
            ),
            provider_model=provider_model,
            provider_request_id=request_id,
        )
