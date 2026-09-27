import uuid

import httpx2
import pytest

from cortex_api.api.deps import ORGANIZATION_HEADER
from cortex_api.models.organization import Organization

from ..conftest import OrganizationFactory

pytestmark = [pytest.mark.database, pytest.mark.redis]


async def test_organization_describes_the_tenant(
    api: httpx2.AsyncClient, headers: dict[str, str], organization: Organization
) -> None:
    body = (await api.get("/v1/organization", headers=headers)).json()
    assert body["id"] == str(organization.id)
    assert (body["name"], body["slug"]) == (organization.name, organization.slug)
    assert body["settings"] == {}

    missing = await api.get("/v1/organization", headers={ORGANIZATION_HEADER: str(uuid.uuid4())})
    assert missing.status_code == 404
    assert (await api.get("/v1/organization")).status_code == 401


class TestConversationList:
    async def test_lists_recent_first_with_counts_and_session_state(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        organization_factory: OrganizationFactory,
    ) -> None:
        quiet = (
            await api.post("/v1/conversations", json={"title": "Quiet"}, headers=headers)
        ).json()
        busy = (await api.post("/v1/conversations", json={"title": "Busy"}, headers=headers)).json()
        gone = (await api.post("/v1/conversations", json={}, headers=headers)).json()
        await api.delete(f"/v1/conversations/{gone['id']}", headers=headers)
        for content in ("One", "Two"):
            await api.post(
                "/v1/messages",
                json={"conversation_id": busy["id"], "role": "user", "content": content},
                headers=headers,
            )

        listed = (await api.get("/v1/conversations", headers=headers)).json()
        assert listed["total"] == 2
        assert [item["id"] for item in listed["items"]] == [busy["id"], quiet["id"]]
        first, second = listed["items"]
        assert (first["message_count"], first["session"]) == (2, "hot")
        assert (second["message_count"], second["session"]) == (0, "cold")
        assert first["title"] == "Busy"

        page = (
            await api.get("/v1/conversations", params={"limit": 1, "offset": 1}, headers=headers)
        ).json()
        assert [item["id"] for item in page["items"]] == [quiet["id"]]
        assert (page["total"], page["limit"], page["offset"]) == (2, 1, 1)

        stranger = {ORGANIZATION_HEADER: str((await organization_factory()).id)}
        assert (await api.get("/v1/conversations", headers=stranger)).json()["total"] == 0


class TestKnowledgeQueries:
    async def test_searches_are_logged_and_replayable(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        organization_factory: OrganizationFactory,
    ) -> None:
        await api.post(
            "/v1/documents",
            json={"title": "Routing", "content": "Cortex routes each request to a provider. " * 30},
            headers=headers,
        )
        search = (
            await api.post(
                "/v1/knowledge/search", json={"query": "how are requests routed"}, headers=headers
            )
        ).json()
        await api.post("/v1/knowledge/search", json={"query": "second"}, headers=headers)

        listed = (await api.get("/v1/knowledge/queries", headers=headers)).json()
        assert listed["total"] == 2
        assert listed["items"][0]["query"] == "second", "newest first"
        assert "results" not in listed["items"][0]

        detail = (
            await api.get(f"/v1/knowledge/queries/{search['query_id']}", headers=headers)
        ).json()
        assert detail["query"] == "how are requests routed"
        assert detail["result_count"] == len(search["results"]) > 0
        assert len(detail["results"]) == detail["result_count"]
        assert detail["completion_id"] is None

        stranger = {ORGANIZATION_HEADER: str((await organization_factory()).id)}
        path = f"/v1/knowledge/queries/{search['query_id']}"
        assert (await api.get(path, headers=stranger)).status_code == 404
        unknown = f"/v1/knowledge/queries/{uuid.uuid4()}"
        assert (await api.get(unknown, headers=headers)).status_code == 404


async def test_executions_record_memory_usage(
    api: httpx2.AsyncClient, headers: dict[str, str]
) -> None:
    conversation = (await api.post("/v1/conversations", json={}, headers=headers)).json()
    await api.post(
        "/v1/messages",
        json={"conversation_id": conversation["id"], "role": "user", "content": "Earlier turn"},
        headers=headers,
    )
    body = (
        await api.post(
            "/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "And now?"}],
                "memory": {"conversation_id": conversation["id"]},
            },
            headers=headers,
        )
    ).json()

    executions = (
        await api.get("/v1/executions", params={"completion_id": body["id"]}, headers=headers)
    ).json()["items"]
    memory = executions[0]["metadata"]["memory"]
    assert memory["conversation_id"] == conversation["id"]
    assert memory["source"] == "cache"
    assert memory["history_messages"] == 1
    assert memory["history_tokens"] > 0
    assert memory["persisted"] is True
    prompt = executions[0]["metadata"]["prompt"]
    assert prompt["messages"] == 2, "history plus the new turn"
