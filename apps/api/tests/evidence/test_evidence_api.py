import uuid
from datetime import datetime
from typing import Any

import httpx2
import pytest

from cortex_api.services.router.base import (
    FinishReason,
    ProviderErrorKind,
    ProviderName,
    ProviderResponse,
)

from ..conftest import OrganizationFactory
from ..router.fakes import FakeProvider

pytestmark = [pytest.mark.database, pytest.mark.redis]

POLICY = """# Refund Policy

## Refunds
Annual plans can be refunded within 30 days of purchase. Refunds take 5 business days.

## Chargebacks
Chargebacks are disputed with the card network.
"""

DECISION: dict[str, Any] = {
    "title": "Approve refund for order 1042",
    "source": "refund-policy-engine@3",
    "metadata": {"order": 1042},
    "evidence": [
        {
            "type": "document",
            "ref_id": "01900000-0000-7000-8000-00000000d0c1",
            "title": "Refund Policy",
            "relation": "supports",
            "relation_confidence": 0.9,
            "explanation": "Order is within the 30-day window",
        },
        {
            "type": "memory",
            "title": "Customer since 2019, no prior disputes",
            "confidence": 0.8,
            "relation": "supports",
            "relation_confidence": 0.5,
            "explanation": "Long-standing customer in good standing",
        },
        {
            "type": "benchmark",
            "title": "Fraud model v7",
            "relation": "contradicts",
            "relation_confidence": 0.2,
            "explanation": "Fraud score slightly elevated",
            "observed_at": "2026-09-01T10:00:00Z",
        },
    ],
}


def answer(output: str) -> ProviderResponse:
    return ProviderResponse(
        output=output,
        prompt_tokens=200,
        completion_tokens=20,
        finish_reason=FinishReason.STOP,
        provider_model="gpt-4.1-2026-01-01",
        provider_request_id="req",
    )


async def record(
    api: httpx2.AsyncClient, headers: dict[str, str], body: dict[str, Any] = DECISION
) -> dict[str, Any]:
    response = await api.post("/v2/evidence/decisions", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


class TestRecordedDecisions:
    async def test_record_and_read_back(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        created = await record(api, headers)
        decision = created["decision"]
        assert decision["type"] == "decision"
        assert decision["title"] == "Approve refund for order 1042"
        assert decision["metadata"]["order"] == 1042
        assert decision["metadata"]["kind"] == "external"
        # noisy-OR(0.9, 0.5 * 0.8) = 0.94, discounted by the 0.2 contradiction.
        assert decision["confidence"] == pytest.approx(0.94 * 0.8)

        supporting = created["supporting"]
        assert [s["node"]["title"] for s in supporting] == [
            "Refund Policy",
            "Customer since 2019, no prior disputes",
        ]
        assert supporting[0]["strength"] == pytest.approx(0.9)
        assert supporting[1]["strength"] == pytest.approx(0.4)
        assert created["counts"] == {"document": 1, "memory": 1}
        [contradiction] = created["contradicting"]
        assert contradiction["node"]["title"] == "Fraud model v7"
        provenance = contradiction["edge"]["provenance"]
        assert provenance == {
            "confidence": 0.2,
            "explanation": "Fraud score slightly elevated",
            "source": "refund-policy-engine@3 via api:organization",
            "timestamp": "2026-09-01T10:00:00Z",
        }

        fetched = await api.get(f"/v2/evidence/{decision['ref_id']}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json() == created

    async def test_ref_id_addresses_the_decision_and_conflicts(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        ref = "01900000-0000-7000-8000-0000000000aa"
        created = await record(api, headers, {**DECISION, "ref_id": ref})
        assert created["decision"]["ref_id"] == ref
        again = await api.post(
            "/v2/evidence/decisions", json={**DECISION, "ref_id": ref}, headers=headers
        )
        assert again.status_code == 409
        assert "already recorded" in again.json()["detail"]

    async def test_explicit_confidence_wins(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        created = await record(api, headers, {**DECISION, "confidence": 0.33})
        assert created["decision"]["confidence"] == 0.33

    @pytest.mark.parametrize(
        "patch",
        [
            {"evidence": []},
            {"title": "  "},
            {"confidence": 1.2},
            {"evidence": [{**DECISION["evidence"][0], "relation_confidence": 2}]},
            {"evidence": [{**DECISION["evidence"][0], "explanation": ""}]},
            {"evidence": [{**DECISION["evidence"][0], "relation": "causes"}]},
            {"evidence": [{**DECISION["evidence"][0], "type": "decision", "ref_id": None}]},
            {"source": "x" * 250},
        ],
    )
    async def test_validation(
        self, api: httpx2.AsyncClient, headers: dict[str, str], patch: dict[str, Any]
    ) -> None:
        response = await api.post(
            "/v2/evidence/decisions", json={**DECISION, **patch}, headers=headers
        )
        assert response.status_code == 422, response.text

    async def test_decision_cannot_cite_itself(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        ref = "01900000-0000-7000-8000-0000000000bb"
        body = {
            **DECISION,
            "ref_id": ref,
            "evidence": [
                {"type": "decision", "ref_id": ref, "title": "Me", "explanation": "circular"}
            ],
        }
        response = await api.post("/v2/evidence/decisions", json=body, headers=headers)
        assert response.status_code == 422
        assert "itself" in response.json()["detail"]

    async def test_decisions_can_build_on_decisions(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        first = await record(api, headers)
        body = {
            "title": "Issue store credit instead",
            "evidence": [
                {
                    "type": "decision",
                    "ref_id": first["decision"]["ref_id"],
                    "title": "ignored: the stored decision keeps its title",
                    "relation": "derived_from",
                    "explanation": "Follows from the approved refund",
                }
            ],
        }
        second = await record(api, headers, body)
        [direct] = [s for s in second["supporting"] if s["depth"] == 1]
        assert direct["node"]["id"] == first["decision"]["id"]
        assert direct["node"]["title"] == "Approve refund for order 1042"
        inherited = {s["node"]["title"] for s in second["supporting"] if s["depth"] == 2}
        assert inherited == {"Refund Policy", "Customer since 2019, no prior disputes"}
        # derived_from is not a support, so it does not raise confidence on its own.
        assert second["decision"]["confidence"] == 0.0


class TestReading:
    async def test_list_decisions(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        for i in range(3):
            await record(api, headers, {**DECISION, "title": f"Decision {i}"})
        page = (await api.get("/v2/evidence", params={"limit": 2}, headers=headers)).json()
        assert page["total"] == 3
        assert (page["limit"], page["offset"]) == (2, 0)
        assert [d["title"] for d in page["items"]] == ["Decision 2", "Decision 1"]
        rest = (
            await api.get("/v2/evidence", params={"limit": 2, "offset": 2}, headers=headers)
        ).json()
        assert [d["title"] for d in rest["items"]] == ["Decision 0"]

    async def test_graph(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        created = await record(api, headers)
        ref = created["decision"]["ref_id"]
        response = await api.get(f"/v2/evidence/{ref}/graph", headers=headers)
        assert response.status_code == 200
        graph = response.json()

        assert graph["root_id"] == created["decision"]["id"]
        assert graph["truncated"] is False
        depths = {n["title"]: n["depth"] for n in graph["nodes"]}
        assert depths["Approve refund for order 1042"] == 0
        assert depths["Refund Policy"] == -1
        assert len(graph["edges"]) == 3
        assert all(e["to_node_id"] == graph["root_id"] for e in graph["edges"])

        timeline = graph["timeline"]
        assert len(timeline) == len(graph["nodes"]) + len(graph["edges"])
        stamps = [datetime.fromisoformat(e["at"]) for e in timeline]
        assert stamps == sorted(stamps)
        assert timeline[0]["label"] == "Fraud model v7 contradicts Approve refund for order 1042"
        assert timeline[0]["source"] == "refund-policy-engine@3 via api:organization"

    async def test_node_detail(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        first = await record(api, headers)
        second = await record(api, headers, {**DECISION, "title": "Second use of the policy"})
        policy = first["supporting"][0]["node"]
        assert second["supporting"][0]["node"]["id"] == policy["id"], "shared evidence"

        detail = (await api.get(f"/v2/evidence/node/{policy['id']}", headers=headers)).json()
        assert detail["node"]["title"] == "Refund Policy"
        assert detail["upstream"] == []
        assert {d["node"]["title"] for d in detail["downstream"]} == {
            "Approve refund for order 1042",
            "Second use of the policy",
        }
        assert all(d["edge"]["provenance"]["explanation"] for d in detail["downstream"])
        assert {d["node"]["id"] for d in detail["decisions"]} == {
            first["decision"]["id"],
            second["decision"]["id"],
        }

        decision = (
            await api.get(f"/v2/evidence/node/{first['decision']['id']}", headers=headers)
        ).json()
        assert len(decision["upstream"]) == 3
        assert decision["decisions"] == []

    async def test_path(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        first = await record(api, headers)
        second = await record(api, headers, {**DECISION, "title": "Second"})
        source, target = first["decision"]["id"], second["decision"]["id"]

        path = (
            await api.get(
                "/v2/evidence/path", params={"source": source, "target": target}, headers=headers
            )
        ).json()
        assert path["connected"] is True
        assert [n["title"] for n in path["nodes"]] == [
            "Approve refund for order 1042",
            "Refund Policy",
            "Second",
        ]
        assert len(path["edges"]) == 2

        directed = (
            await api.get(
                "/v2/evidence/path",
                params={"source": source, "target": target, "directed": True},
                headers=headers,
            )
        ).json()
        assert directed == {
            "source_id": source,
            "target_id": target,
            "connected": False,
            "edges": [],
            "nodes": [],
        }

    async def test_not_found_and_tenant_isolation(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        organization_factory: OrganizationFactory,
    ) -> None:
        created = await record(api, headers)
        ref, node_id = created["decision"]["ref_id"], created["decision"]["id"]
        other = {"X-Organization-ID": str((await organization_factory()).id)}

        for url in (
            f"/v2/evidence/{ref}",
            f"/v2/evidence/{ref}/graph",
            f"/v2/evidence/node/{node_id}",
        ):
            response = await api.get(url, headers=other)
            assert response.status_code == 404, url
        assert (await api.get(f"/v2/evidence/{ref}", headers=headers)).status_code == 200
        missing = await api.get(f"/v2/evidence/{uuid.uuid4()}", headers=headers)
        assert missing.json() == {"detail": "Decision not found"}
        assert (await api.get(f"/v2/evidence/node/{uuid.uuid4()}", headers=headers)).json() == {
            "detail": "Evidence node not found"
        }
        assert (await api.get("/v2/evidence", headers=other)).json()["total"] == 0

    async def test_depth_is_bounded(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        created = await record(api, headers)
        ref = created["decision"]["ref_id"]
        assert (
            await api.get(f"/v2/evidence/{ref}/graph?depth=11", headers=headers)
        ).status_code == 422
        assert (
            await api.get(f"/v2/evidence/{ref}/graph?depth=0", headers=headers)
        ).status_code == 422

    async def test_requires_a_tenant(self, api: httpx2.AsyncClient) -> None:
        assert (await api.get("/v2/evidence")).status_code in (400, 401, 422)


class TestCompletionsAreTraceable:
    async def test_grounded_completion_with_memory(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        providers: dict[ProviderName, FakeProvider],
    ) -> None:
        document = await api.post(
            "/v1/documents",
            json={"title": "Refund Policy", "content": POLICY, "mime_type": "text/markdown"},
            headers=headers,
        )
        assert document.status_code == 201, document.text
        memory = await api.post(
            "/v1/memories",
            json={
                "type": "semantic",
                "content": "Refunds for annual plans are the most common support request.",
                "importance": 0.8,
            },
            headers=headers,
        )
        assert memory.status_code == 201, memory.text
        conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
        await api.post(
            "/v1/messages",
            json={
                "conversation_id": conversation["id"],
                "role": "user",
                "content": "I bought an annual plan last week.",
            },
            headers=headers,
        )

        providers[ProviderName.OPENAI].script.append(answer("Refunds take 5 business days [1]."))
        completion = await api.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4.1",
                "messages": [
                    {"role": "user", "content": "How long do refunds for annual plans take?"}
                ],
                "knowledge": {"top_k": 3},
                "memory": {"conversation_id": conversation["id"]},
            },
            headers=headers,
        )
        assert completion.status_code == 200, completion.text
        completion_id = completion.json()["id"]

        evidence = (await api.get(f"/v2/evidence/{completion_id}", headers=headers)).json()
        decision = evidence["decision"]
        assert decision["ref_id"] == completion_id
        assert decision["title"] == "Answer: How long do refunds for annual plans take?"
        assert decision["metadata"]["model"] == "gpt-4.1"
        assert decision["confidence"] > 0

        types = {s["node"]["type"] for s in evidence["supporting"]}
        assert {"knowledge", "chunk", "document", "memory", "message", "benchmark"} <= types
        cited = [
            s for s in evidence["supporting"] if s["node"]["type"] == "chunk" and s["depth"] == 1
        ]
        assert cited, "the cited chunk supports the decision directly"
        assert "Refunds" in cited[0]["node"]["title"]

        graph = (await api.get(f"/v2/evidence/{completion_id}/graph", headers=headers)).json()
        edges = graph["edges"]
        by_id = {n["id"]: n for n in graph["nodes"]}
        relations = {
            (by_id[e["from_node_id"]]["type"], e["type"], by_id[e["to_node_id"]]["type"])
            for e in edges
        }
        assert ("benchmark", "generated_by", "decision") in relations
        assert ("knowledge", "supports", "decision") in relations
        assert ("chunk", "retrieved_from", "knowledge") in relations
        assert ("document", "derived_from", "chunk") in relations
        assert ("chunk", "supports", "decision") in relations
        assert ("memory", "supports", "decision") in relations
        assert ("conversation", "references", "decision") in relations
        assert ("message", "derived_from", "decision") in relations
        assert ("decision", "generated_by", "message") in relations, "the stored reply"
        assert all(e["provenance"]["source"].startswith("cortex.") for e in edges)
        assert all(e["provenance"]["explanation"].strip() for e in edges)

        reply = next(n for n in graph["nodes"] if n["type"] == "message" and n["depth"] == 1)
        assert reply["metadata"]["role"] == "assistant"
        prompts = [
            n["metadata"]["excerpt"]
            for n in graph["nodes"]
            if n["type"] == "message" and n["depth"] < 0
        ]
        assert "I bought an annual plan last week." in prompts
        assert "How long do refunds for annual plans take?" in prompts

    async def test_plain_completion_is_recorded_without_support(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        providers: dict[ProviderName, FakeProvider],
    ) -> None:
        providers[ProviderName.OPENAI].script.append(answer("Hello."))
        completion = await api.post(
            "/v1/chat/completions",
            json={"model": "gpt-4.1", "messages": [{"role": "user", "content": "Hi"}]},
            headers=headers,
        )
        assert completion.status_code == 200, completion.text
        evidence = (
            await api.get(f"/v2/evidence/{completion.json()['id']}", headers=headers)
        ).json()
        assert evidence["decision"]["confidence"] == 0.0, "nothing corroborates it"
        [generation] = evidence["supporting"]
        assert generation["node"]["type"] == "benchmark"
        assert generation["node"]["metadata"]["provider"] == "openai"

    async def test_failed_completion_records_no_decision(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        providers: dict[ProviderName, FakeProvider],
    ) -> None:
        for provider in providers.values():
            provider.fail(ProviderErrorKind.SERVER, times=10)
        response = await api.post(
            "/v1/chat/completions",
            json={"model": "gpt-4.1", "messages": [{"role": "user", "content": "Hi"}]},
            headers=headers,
        )
        assert response.status_code == 502
        assert (await api.get("/v2/evidence", headers=headers)).json()["total"] == 0
