import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx2
import pytest

from cortex_api.models.message import MessageRole
from cortex_api.services.router.base import (
    ChatMessage,
    FinishReason,
    HTTPProvider,
    HTTPProviderConfig,
    ModelSpec,
    ProviderError,
    ProviderErrorKind,
    ProviderName,
    ProviderRequest,
    ProviderResponse,
)
from cortex_api.services.router.providers.anthropic_provider import (
    ANTHROPIC_MODELS,
    AnthropicProvider,
)
from cortex_api.services.router.providers.gemini_provider import GEMINI_MODELS, GeminiProvider
from cortex_api.services.router.providers.openai_provider import OPENAI_MODELS, OpenAIProvider

Handler = Callable[[httpx2.Request], httpx2.Response]

CONVERSATION = (
    ChatMessage(MessageRole.SYSTEM, "Be brief."),
    ChatMessage(MessageRole.USER, "Hi"),
    ChatMessage(MessageRole.ASSISTANT, "Hello"),
    ChatMessage(MessageRole.TOOL, "42"),
    ChatMessage(MessageRole.USER, "What next?"),
)


class Recorder:
    def __init__(self, status: int = 200, body: Any = None, headers: dict[str, str] | None = None):
        self.status, self.body, self.headers = status, body, headers or {}
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if isinstance(self.body, (dict, list)):
            return httpx2.Response(self.status, json=self.body, headers=self.headers)
        return httpx2.Response(self.status, text=self.body or "", headers=self.headers)

    @property
    def payload(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(self.requests[-1].content)
        return result


def make(
    factory: type[HTTPProvider],
    handler: Handler,
    *,
    api_key: str | None = "test-key",
    **kwargs: Any,
) -> HTTPProvider:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    base_urls = {
        OpenAIProvider: "https://api.openai.test/v1",
        AnthropicProvider: "https://api.anthropic.test/v1",
        GeminiProvider: "https://gemini.test/v1beta",
    }
    config = HTTPProviderConfig(api_key=api_key, base_url=base_urls.get(factory, "https://x.test"))
    return factory(client=client, config=config, **kwargs)


def request(model: ModelSpec, *, temperature: float | None = 0.3) -> ProviderRequest:
    return ProviderRequest(model, CONVERSATION, max_tokens=256, temperature=temperature)


# --- OpenAI (and compatible hosts) ---------------------------------------------------


async def test_openai_request_and_unified_response() -> None:
    recorder = Recorder(
        body={
            "id": "chatcmpl-1",
            "model": "gpt-4.1-2025-04-14",
            "choices": [
                {"message": {"role": "assistant", "content": "Done."}, "finish_reason": "length"}
            ],
            "usage": {"prompt_tokens": 21, "completion_tokens": 5},
        }
    )
    provider = make(OpenAIProvider, recorder)

    response = await provider.complete(request(OPENAI_MODELS[0]))

    sent = recorder.requests[0]
    assert str(sent.url) == "https://api.openai.test/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-key"
    assert recorder.payload == {
        "model": "gpt-4.1",
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Tool result:\n42\n\nWhat next?"},
        ],
        "max_completion_tokens": 256,
        "temperature": 0.3,
    }
    assert response == ProviderResponse(
        output="Done.",
        prompt_tokens=21,
        completion_tokens=5,
        finish_reason=FinishReason.LENGTH,
        provider_model="gpt-4.1-2025-04-14",
        provider_request_id="chatcmpl-1",
    )


async def test_openai_reasoning_models_omit_temperature_and_map_refusals() -> None:
    recorder = Recorder(
        body={
            "choices": [
                {"message": {"content": None, "refusal": "I can't help."}, "finish_reason": "stop"}
            ]
        }
    )
    o4_mini = next(s for s in OPENAI_MODELS if s.id == "o4-mini")
    response = await make(OpenAIProvider, recorder).complete(request(o4_mini))
    assert "temperature" not in recorder.payload
    assert response.output == "I can't help."
    assert response.finish_reason is FinishReason.CONTENT_FILTER
    assert (response.prompt_tokens, response.completion_tokens) == (0, 0)


async def test_openai_compatible_host_is_a_registration_not_new_code() -> None:
    deepseek = ModelSpec(
        id="deepseek-chat",
        provider=ProviderName.DEEPSEEK,
        display_name="DeepSeek V3",
        context_window=64_000,
        max_output_tokens=8_000,
        input_cost_per_mtok=Decimal("0.27"),
        output_cost_per_mtok=Decimal("1.10"),
        quality=4,
        typical_latency_ms=2_000,
    )
    recorder = Recorder(
        body={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
    )
    provider = OpenAIProvider(
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder)),
        config=HTTPProviderConfig(api_key="ds-key", base_url="https://api.deepseek.test/v1/"),
        models=[deepseek],
        name=ProviderName.DEEPSEEK,
        max_tokens_field="max_tokens",
    )
    response = await provider.complete(request(deepseek))
    assert provider.name is ProviderName.DEEPSEEK
    assert str(recorder.requests[0].url) == "https://api.deepseek.test/v1/chat/completions"
    assert recorder.payload["max_tokens"] == 256
    assert response.output == "ok"


# --- Anthropic ---------------------------------------------------------------------


async def test_anthropic_request_and_unified_response() -> None:
    recorder = Recorder(
        body={
            "id": "msg_1",
            "model": "claude-sonnet-4-5-20250929",
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "Hello "},
                {"type": "text", "text": "there"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 30, "output_tokens": 4},
        }
    )
    provider = make(AnthropicProvider, recorder)

    response = await provider.complete(request(ANTHROPIC_MODELS[0], temperature=1.7))

    sent = recorder.requests[0]
    assert str(sent.url) == "https://api.anthropic.test/v1/messages"
    assert sent.headers["x-api-key"] == "test-key"
    assert sent.headers["anthropic-version"] == "2023-06-01"
    assert recorder.payload == {
        "model": "claude-sonnet-4-5",
        "max_tokens": 256,
        "system": "Be brief.",
        "temperature": 1.0,
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "Tool result:\n42\n\nWhat next?"},
        ],
    }
    assert response == ProviderResponse(
        output="Hello there",
        prompt_tokens=30,
        completion_tokens=4,
        finish_reason=FinishReason.STOP,
        provider_model="claude-sonnet-4-5-20250929",
        provider_request_id="msg_1",
    )


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("max_tokens", FinishReason.LENGTH),
        ("tool_use", FinishReason.TOOL_CALLS),
        ("refusal", FinishReason.CONTENT_FILTER),
        ("pause_turn", FinishReason.OTHER),
    ],
)
async def test_anthropic_finish_reasons(stop_reason: str, expected: FinishReason) -> None:
    recorder = Recorder(body={"content": [], "stop_reason": stop_reason})
    response = await make(AnthropicProvider, recorder).complete(request(ANTHROPIC_MODELS[1]))
    assert response.finish_reason is expected


# --- Gemini --------------------------------------------------------------------------


async def test_gemini_request_and_unified_response() -> None:
    recorder = Recorder(
        body={
            "responseId": "r-1",
            "modelVersion": "gemini-2.5-flash",
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": "pondering", "thought": True}, {"text": "Answer"}],
                    },
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 3,
                "thoughtsTokenCount": 7,
            },
        }
    )
    flash = next(s for s in GEMINI_MODELS if s.id == "gemini-2.5-flash")
    response = await make(GeminiProvider, recorder).complete(request(flash))

    sent = recorder.requests[0]
    assert str(sent.url) == "https://gemini.test/v1beta/models/gemini-2.5-flash:generateContent"
    assert sent.headers["x-goog-api-key"] == "test-key"
    assert "key=" not in str(sent.url), "API key never goes in the URL"
    assert recorder.payload == {
        "contents": [
            {"role": "user", "parts": [{"text": "Hi"}]},
            {"role": "model", "parts": [{"text": "Hello"}]},
            {"role": "user", "parts": [{"text": "Tool result:\n42\n\nWhat next?"}]},
        ],
        "systemInstruction": {"parts": [{"text": "Be brief."}]},
        "generationConfig": {"maxOutputTokens": 256, "temperature": 0.3},
    }
    assert response == ProviderResponse(
        output="Answer",
        prompt_tokens=12,
        completion_tokens=10,
        finish_reason=FinishReason.LENGTH,
        provider_model="gemini-2.5-flash",
        provider_request_id="r-1",
    )


async def test_gemini_blocked_prompt_is_a_content_filter_not_an_error() -> None:
    recorder = Recorder(
        body={"promptFeedback": {"blockReason": "SAFETY"}, "usageMetadata": {"promptTokenCount": 9}}
    )
    response = await make(GeminiProvider, recorder).complete(request(GEMINI_MODELS[0]))
    assert response.output == ""
    assert response.finish_reason is FinishReason.CONTENT_FILTER
    assert response.prompt_tokens == 9


# --- Error classification (shared transport) --------------------------------------------

ADAPTERS = [
    (OpenAIProvider, OPENAI_MODELS[0]),
    (AnthropicProvider, ANTHROPIC_MODELS[0]),
    (GeminiProvider, GEMINI_MODELS[0]),
]


@pytest.mark.parametrize(("factory", "model"), ADAPTERS)
@pytest.mark.parametrize(
    ("status", "body", "headers", "kind", "retryable"),
    [
        (401, {"error": {"message": "bad key"}}, {}, ProviderErrorKind.AUTHENTICATION, False),
        (
            429,
            {"error": {"message": "slow down"}},
            {"retry-after": "2"},
            ProviderErrorKind.RATE_LIMITED,
            True,
        ),
        (500, "gateway on fire", {}, ProviderErrorKind.SERVER, True),
        (
            529,
            {"type": "error", "error": {"message": "Overloaded"}},
            {},
            ProviderErrorKind.SERVER,
            True,
        ),
        (400, {"error": {"message": "context too long"}}, {}, ProviderErrorKind.BAD_REQUEST, False),
        (408, "", {}, ProviderErrorKind.TIMEOUT, True),
    ],
)
async def test_http_errors_are_classified(
    factory: type[HTTPProvider],
    model: ModelSpec,
    status: int,
    body: Any,
    headers: dict[str, str],
    kind: ProviderErrorKind,
    retryable: bool,
) -> None:
    provider = make(factory, Recorder(status, body, headers))
    with pytest.raises(ProviderError) as info:
        await provider.complete(request(model))
    error = info.value
    assert error.kind is kind
    assert error.retryable is retryable
    assert error.status_code == status
    assert error.provider is provider.name
    if isinstance(body, dict):
        assert error.message == body["error"]["message"]
    if headers:
        assert error.retry_after_seconds == 2.0


@pytest.mark.parametrize(("factory", "model"), ADAPTERS)
@pytest.mark.parametrize(
    ("exception", "kind"),
    [
        (httpx2.ReadTimeout, ProviderErrorKind.TIMEOUT),
        (httpx2.ConnectError, ProviderErrorKind.CONNECTION),
    ],
)
async def test_transport_failures_are_classified(
    factory: type[HTTPProvider],
    model: ModelSpec,
    exception: type[Exception],
    kind: ProviderErrorKind,
) -> None:
    def explode(req: httpx2.Request) -> httpx2.Response:
        raise exception("network trouble", request=req)  # type: ignore[call-arg]

    with pytest.raises(ProviderError) as info:
        await make(factory, explode).complete(request(model))
    assert info.value.kind is kind
    assert info.value.retryable


@pytest.mark.parametrize(("factory", "model"), ADAPTERS)
async def test_malformed_bodies_are_invalid_responses(
    factory: type[HTTPProvider], model: ModelSpec
) -> None:
    for body in ("<html>not json</html>", ["not", "an", "object"], {"unexpected": True}):
        with pytest.raises(ProviderError) as info:
            await make(factory, Recorder(200, body)).complete(request(model))
        assert info.value.kind is ProviderErrorKind.INVALID_RESPONSE


@pytest.mark.parametrize(("factory", "model"), ADAPTERS)
async def test_unconfigured_provider_never_calls_upstream(
    factory: type[HTTPProvider], model: ModelSpec
) -> None:
    recorder = Recorder(body={})
    provider = make(factory, recorder, api_key=None)
    assert not provider.configured
    with pytest.raises(ProviderError) as info:
        await provider.complete(request(model))
    assert info.value.kind is ProviderErrorKind.UNAVAILABLE
    assert recorder.requests == []
