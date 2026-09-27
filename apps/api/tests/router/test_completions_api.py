from typing import Any

import httpx2
import pytest

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.message import MessageRole
from cortex_api.schemas.completion import CompletionResponse
from cortex_api.services.router.base import ProviderName

from ..conftest import OrganizationFactory
from .fakes import FakeProvider

pytestmark = [pytest.mark.database, pytest.mark.redis]

HELLO: dict[str, Any] = {"messages": [{"role": "user", "content": "Hello, Cortex"}]}
RESPONSE_FIELDS = set(CompletionResponse.model_fields)


async def complete(
    api: httpx2.AsyncClient, headers: dict[str, str], **body: Any
) -> httpx2.Response:
    return await api.post("/v1/chat/completions", json={**HELLO, **body}, headers=headers)


async def test_unified_response_schema(api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
    response = await complete(api, headers, metadata={"trace": "abc"})
    assert response.status_code == 200
    body = response.json()

    assert set(body) == RESPONSE_FIELDS
    assert body["object"] == "cortex.completion"
    assert body["provider"] in {"openai", "anthropic", "gemini"}
    assert body["output"] == f"{body['provider']} answered with {body['model']}"
    assert body["tokens"] == {"prompt": 120, "completion": 30, "total": 150}
    assert body["finish_reason"] == "stop"
    assert body["routing_mode"] == "auto"
    assert body["routing_reason"].startswith(f"auto: {body['provider']}/{body['model']} scored")
    assert body["latency_ms"] >= 0
    assert body["cost_estimate"] > 0
    assert body["attempts"] == [
        {
            "provider": body["provider"],
            "model": body["model"],
            "success": True,
            "latency_ms": body["attempts"][0]["latency_ms"],
            "error": None,
        }
    ]
    CompletionResponse.model_validate(body)


@pytest.mark.parametrize("model", ["gpt-4.1-mini", "claude-haiku-4-5", "gemini/gemini-2.5-flash"])
async def test_every_provider_returns_the_same_shape(
    api: httpx2.AsyncClient, headers: dict[str, str], model: str
) -> None:
    response = await complete(api, headers, model=model)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_FIELDS
    assert body["model"] == model.split("/")[-1]
    assert body["routing_mode"] == "preferred"


async def test_fallback_is_transparent_to_the_caller(
    api: httpx2.AsyncClient, headers: dict[str, str], providers: dict[ProviderName, FakeProvider]
) -> None:
    providers[ProviderName.OPENAI].fail(times=2)
    response = await complete(api, headers, model="gpt-4.1")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "anthropic"
    assert [a["success"] for a in body["attempts"]] == [False, False, True]
    assert body["attempts"][0]["error"] == "server_error (HTTP 500): upstream exploded"
    assert body["routing_reason"] == (
        "preferred: openai/gpt-4.1 as requested; "
        f"fell back to anthropic/{body['model']} after openai/gpt-4.1 server_error"
    )


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"messages": []}, "at least 1"),
        ({"messages": [{"role": "system", "content": "rules"}]}, "non-system"),
        ({**HELLO, "routing": {"mode": "strict"}}, "strict routing requires"),
        ({**HELLO, "routing": {"mode": "preferred"}}, "preferred routing requires"),
        ({**HELLO, "temperature": 3}, "less than or equal to 2"),
        ({**HELLO, "metadata": {"blob": "x" * 20_000}}, "metadata must serialize"),
        ({**HELLO, "routing": {"provider": "aol"}}, "provider"),
    ],
)
async def test_request_validation(
    api: httpx2.AsyncClient, headers: dict[str, str], body: dict[str, Any], fragment: str
) -> None:
    response = await api.post("/v1/chat/completions", json=body, headers=headers)
    assert response.status_code == 422
    assert fragment in response.text


async def test_unknown_model_and_missing_tenant(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    unknown = await complete(api, headers, model="gpt-17")
    assert unknown.status_code == 422
    assert unknown.json() == {"detail": "Model 'gpt-17' is not in the catalog"}
    assert (await api.post("/v1/chat/completions", json=HELLO)).status_code == 401


async def test_organization_policy_is_enforced(
    api: httpx2.AsyncClient, organization_factory: OrganizationFactory
) -> None:
    org = await organization_factory({"routing": {"allowed_providers": ["gemini"]}})
    scoped = {ORGANIZATION_HEADER: str(org.id)}

    auto = await complete(api, scoped)
    assert auto.json()["provider"] == "gemini"
    assert len(auto.json()["attempts"]) == 1

    denied = await complete(api, scoped, model="gpt-4.1", routing={"mode": "strict"})
    assert denied.status_code == 403
    assert "not allowed by organization policy" in denied.json()["detail"]

    broken = await organization_factory({"routing": {"allowed_providers": "everything"}})
    invalid = await complete(api, {ORGANIZATION_HEADER: str(broken.id)})
    assert invalid.status_code == 500
    assert "routing policy is invalid" in invalid.json()["detail"]


async def test_no_available_provider_is_503(
    api: httpx2.AsyncClient, headers: dict[str, str], providers: dict[ProviderName, FakeProvider]
) -> None:
    for provider in providers.values():
        provider.is_configured = False
    response = await complete(api, headers)
    assert response.status_code == 503
    body = response.json()
    assert body["detail"] == "no eligible model for this request"
    assert body["rejections"]["openai/gpt-4.1"] == "provider not_configured"


async def test_list_models(
    api: httpx2.AsyncClient,
    organization_factory: OrganizationFactory,
    providers: dict[ProviderName, FakeProvider],
) -> None:
    providers[ProviderName.ANTHROPIC].is_configured = False
    org = await organization_factory({"routing": {"blocked_models": ["gemini-2.5-pro"]}})

    response = await api.get("/v1/models", headers={ORGANIZATION_HEADER: str(org.id)})
    assert response.status_code == 200
    models = {m["id"]: m for m in response.json()["models"]}
    assert len(models) == 9

    gpt = models["openai/gpt-4.1"]
    assert gpt == {
        "id": "openai/gpt-4.1",
        "provider": "openai",
        "model": "gpt-4.1",
        "display_name": "GPT-4.1",
        "capabilities": ["chat", "json_mode", "tools", "vision"],
        "context_window": 1_047_576,
        "max_output_tokens": 32_768,
        "pricing": {"input_per_mtok": 2.0, "output_per_mtok": 8.0, "currency": "USD"},
        "quality": 4,
        "expected_latency_ms": 2_500,
        "available": True,
        "availability": "ok",
        "allowed": True,
    }
    assert models["anthropic/claude-sonnet-4-5"]["availability"] == "not_configured"
    assert models["gemini/gemini-2.5-pro"]["allowed"] is False


async def test_completion_grounded_in_memory(
    api: httpx2.AsyncClient, headers: dict[str, str], providers: dict[ProviderName, FakeProvider]
) -> None:
    await api.post(
        "/v1/memories",
        json={"type": "preference", "content": "The customer prefers replies in French."},
        headers=headers,
    )
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    await api.post(
        "/v1/messages",
        json={"conversation_id": conversation["id"], "role": "user", "content": "Bonjour"},
        headers=headers,
    )

    response = await complete(
        api,
        headers,
        model="gpt-4.1",
        messages=[
            {"role": "system", "content": "You are Cortex."},
            {"role": "user", "content": "Which language do replies use?"},
        ],
        memory={"conversation_id": conversation["id"]},
    )
    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation["id"]

    sent = providers[ProviderName.OPENAI].calls[-1]
    first = sent.messages[0]
    assert (first.role, first.content) == (MessageRole.SYSTEM, "You are Cortex.")
    assert sent.messages[1].role is MessageRole.SYSTEM
    assert "[preference] The customer prefers replies in French." in sent.messages[1].content
    assert [m.content for m in sent.messages[2:]] == ["Bonjour", "Which language do replies use?"]

    detail = (await api.get(f"/v1/conversations/{conversation['id']}", headers=headers)).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Bonjour"),
        ("user", "Which language do replies use?"),
        ("assistant", "openai answered with gpt-4.1"),
    ]
    assistant = detail["messages"][-1]
    assert assistant["metadata"] == {
        "completion_id": response.json()["id"],
        "provider": "openai",
        "model": "gpt-4.1",
    }


async def test_memory_for_unknown_conversation_is_404_before_any_provider_call(
    api: httpx2.AsyncClient, headers: dict[str, str], providers: dict[ProviderName, FakeProvider]
) -> None:
    response = await complete(
        api, headers, memory={"conversation_id": "01900000-0000-7000-8000-000000000000"}
    )
    assert response.status_code == 404
    assert all(not p.calls for p in providers.values())
