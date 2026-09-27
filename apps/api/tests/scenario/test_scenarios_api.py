import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.assumption import Assumption
from cortex_api.models.evidence_node import EvidenceNode
from cortex_api.models.organization import Organization
from cortex_api.models.outcome import Outcome
from cortex_api.models.scenario import Scenario

from ..conftest import OrganizationFactory
from ..evidence.test_evidence_api import DECISION, record

pytestmark = [pytest.mark.database, pytest.mark.redis]

TYPES = {"best_case", "base_case", "worst_case", "aggressive", "conservative"}
CRITERIA = {
    "evidence_quality",
    "uncertainty",
    "constraint_satisfaction",
    "objective_alignment",
    "risk_exposure",
}


async def simulate(
    api: httpx2.AsyncClient, headers: dict[str, str], decision_id: str, **body: Any
) -> dict[str, Any]:
    response = await api.post(
        "/v2/scenarios", json={"decision_id": decision_id, **body}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.fixture
async def decision(api: httpx2.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    return (await record(api, headers))["decision"]  # type: ignore[no-any-return]


class TestGeneration:
    async def test_generates_five_ranked_scenarios(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(
            api,
            headers,
            decision["ref_id"],
            objective="Keep the customer without losing money",
            constraints=[{"statement": "Refund under $500", "severity": "hard"}],
            assumptions=[{"statement": "Finance approves the budget", "confidence": 0.9}],
        )
        scenarios = simulation["scenarios"]
        assert {s["type"] for s in scenarios} == TYPES
        assert [s["rank"] for s in scenarios] == [1, 2, 3, 4, 5]
        assert simulation["recommended_id"] == scenarios[0]["id"]
        assert simulation["decision_id"] == decision["ref_id"]
        assert simulation["objective"] == "Keep the customer without losing money"
        assert simulation["parameters"]["constraints"] == [
            {"statement": "Refund under $500", "severity": "hard"}
        ]
        for scenario in scenarios:
            assert 0 <= scenario["score"] <= 1
            assert 0 <= scenario["confidence"] <= 1
            assert set(scenario["criteria"]) == CRITERIA
            assert sum(c["contribution"] for c in scenario["criteria"].values()) == pytest.approx(
                scenario["score"]
            )
            assert scenario["description"]
        scores = [s["score"] for s in scenarios]
        assert scores == sorted(scores, reverse=True)

    async def test_best_base_worst_order_their_outcomes(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(api, headers, decision["ref_id"])
        by_type = {s["type"]: s for s in simulation["scenarios"]}
        likelihoods = [
            by_type[t]["success_likelihood"] for t in ("best_case", "base_case", "worst_case")
        ]
        assert likelihoods == sorted(likelihoods, reverse=True)
        assert by_type["best_case"]["score"] > by_type["worst_case"]["score"]
        for scenario in simulation["scenarios"]:
            outcomes = scenario["outcomes"]
            met = next(o for o in outcomes if o["kind"] == "objective_met")
            assert met["likelihood"] == pytest.approx(scenario["success_likelihood"])
            assert scenario["expected_impact"] == pytest.approx(
                sum(o["expected_impact"] for o in outcomes)
            )

    async def test_subset_of_types_and_custom_weights(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(
            api,
            headers,
            decision["ref_id"],
            types=["aggressive", "conservative"],
            weights={"objective_alignment": 1, "risk_exposure": 0},
            risk_tolerance=1,
        )
        assert [s["type"] for s in simulation["scenarios"]] == ["aggressive", "conservative"]
        weights = simulation["parameters"]["weights"]
        assert weights["risk_exposure"] == 0
        assert sum(weights.values()) == pytest.approx(1)

    async def test_decision_without_evidence_still_plans(
        self, api: httpx2.AsyncClient, headers: dict[str, str]
    ) -> None:
        bare = await record(
            api,
            headers,
            {
                "title": "Ship on Friday",
                "evidence": [
                    {
                        "type": "memory",
                        "title": "Last Friday release broke checkout",
                        "relation": "contradicts",
                        "relation_confidence": 0.7,
                        "explanation": "Weekend incidents are hard to staff",
                    }
                ],
            },
        )
        simulation = await simulate(api, headers, bare["decision"]["ref_id"])
        for scenario in simulation["scenarios"]:
            assert scenario["success_likelihood"] == 0
            assert scenario["criteria"]["evidence_quality"]["value"] == 0


class TestAssumptionLinking:
    async def test_assumptions_point_at_evidence_nodes(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(
            api,
            headers,
            decision["ref_id"],
            constraints=[{"statement": "Refund under $500", "severity": "hard"}],
        )
        base = next(s for s in simulation["scenarios"] if s["type"] == "base_case")
        evidence = (await api.get(f"/v2/evidence/{decision['ref_id']}", headers=headers)).json()
        supporting = {item["node"]["id"]: item for item in evidence["supporting"]}
        contradicting = {item["node"]["id"] for item in evidence["contradicting"]}

        linked = [a for a in base["assumptions"] if a["kind"] == "evidence"]
        assert {a["evidence_node_id"] for a in linked} == set(supporting)
        for assumption in linked:
            assert assumption["source"] == "cortex.evidence"
            assert assumption["baseline_confidence"] == pytest.approx(
                supporting[assumption["evidence_node_id"]]["strength"]
            )
        objections = [a for a in base["assumptions"] if a["kind"] == "contradiction"]
        assert {a["evidence_node_id"] for a in objections} == contradicting

        constraint = next(a for a in base["assumptions"] if a["kind"] == "constraint")
        assert constraint["source"].startswith("api:")
        breach = next(o for o in base["outcomes"] if o["kind"] == "constraint_breach")
        assert breach["assumption_id"] == constraint["id"]
        assert breach["likelihood"] == pytest.approx(1 - constraint["confidence"])

    async def test_evidence_node_deletion_keeps_the_assumption(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        decision: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        simulation = await simulate(api, headers, decision["ref_id"])
        scenario = simulation["scenarios"][0]
        linked = next(a for a in scenario["assumptions"] if a["evidence_node_id"])
        await session.execute(
            delete(EvidenceNode).where(EvidenceNode.id == uuid.UUID(linked["evidence_node_id"]))
        )
        await session.commit()
        session.expire_all()
        again = (await api.get(f"/v2/scenarios/{scenario['id']}", headers=headers)).json()
        kept = next(a for a in again["assumptions"] if a["id"] == linked["id"])
        assert kept["evidence_node_id"] is None
        assert kept["statement"] == linked["statement"]

    async def test_deleting_the_decision_deletes_its_scenarios(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        decision: dict[str, Any],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        await simulate(api, headers, decision["ref_id"])
        await session.execute(
            delete(EvidenceNode).where(EvidenceNode.id == uuid.UUID(decision["id"]))
        )
        await session.commit()
        for model in (Scenario, Assumption, Outcome):
            count = await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.organization_id == organization.id)
            )
            assert count == 0, model.__name__


class TestReading:
    async def test_get_scenario(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(api, headers, decision["ref_id"])
        created = simulation["scenarios"][2]
        response = await api.get(f"/v2/scenarios/{created['id']}", headers=headers)
        assert response.status_code == 200
        assert response.json() == created

    async def test_decision_scenarios_newest_first(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        first = await simulate(api, headers, decision["ref_id"], objective="First")
        second = await simulate(api, headers, decision["ref_id"], objective="Second")
        response = await api.get(f"/v2/decisions/{decision['ref_id']}/scenarios", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert [s["simulation_id"] for s in body["simulations"]] == [
            second["simulation_id"],
            first["simulation_id"],
        ]
        assert body["simulations"][0] == second

        page = await api.get(
            f"/v2/decisions/{decision['ref_id']}/scenarios",
            params={"limit": 1, "offset": 1},
            headers=headers,
        )
        assert [s["objective"] for s in page.json()["simulations"]] == ["First"]

    async def test_decision_without_simulations(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        response = await api.get(f"/v2/decisions/{decision['ref_id']}/scenarios", headers=headers)
        assert response.json() == {
            "decision_id": decision["ref_id"],
            "simulations": [],
            "total": 0,
            "limit": 5,
            "offset": 0,
        }

    async def test_list_simulations(
        self, api: httpx2.AsyncClient, headers: dict[str, str], decision: dict[str, Any]
    ) -> None:
        simulation = await simulate(api, headers, decision["ref_id"], types=["base_case"])
        body = (await api.get("/v2/scenarios", headers=headers)).json()
        assert body["total"] == 1
        [item] = body["items"]
        assert item["simulation_id"] == simulation["simulation_id"]
        assert item["decision_title"] == DECISION["title"]
        assert item["scenario_count"] == 1
        assert item["recommended"]["type"] == "base_case"


class TestErrors:
    async def test_unknown_decision(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        missing = str(uuid.uuid4())
        response = await api.post("/v2/scenarios", json={"decision_id": missing}, headers=headers)
        assert response.status_code == 404
        assert response.json() == {"detail": "Decision not found"}
        listed = await api.get(f"/v2/decisions/{missing}/scenarios", headers=headers)
        assert listed.status_code == 404

    async def test_unknown_scenario(self, api: httpx2.AsyncClient, headers: dict[str, str]) -> None:
        response = await api.get(f"/v2/scenarios/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"types": ["base_case", "base_case"]},
            {"types": []},
            {"types": ["moonshot"]},
            {"risk_tolerance": 1.5},
            {"weights": {"uncertainty": -1}},
            {
                "weights": {
                    "evidence_quality": 0,
                    "uncertainty": 0,
                    "constraint_satisfaction": 0,
                    "objective_alignment": 0,
                    "risk_exposure": 0,
                }
            },
            {"constraints": [{"statement": " ", "severity": "hard"}]},
            {"constraints": [{"statement": "x", "severity": "fatal"}]},
            {"assumptions": [{"statement": "x", "confidence": 2}]},
            {"depth": 0},
        ],
    )
    async def test_validation(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        decision: dict[str, Any],
        body: dict[str, Any],
    ) -> None:
        response = await api.post(
            "/v2/scenarios", json={"decision_id": decision["ref_id"], **body}, headers=headers
        )
        assert response.status_code == 422, body

    async def test_organizations_are_isolated(
        self,
        api: httpx2.AsyncClient,
        headers: dict[str, str],
        decision: dict[str, Any],
        organization_factory: OrganizationFactory,
    ) -> None:
        simulation = await simulate(api, headers, decision["ref_id"])
        other = await organization_factory()
        theirs = {"X-Organization-ID": str(other.id)}
        scenario_id = simulation["scenarios"][0]["id"]
        assert (await api.get(f"/v2/scenarios/{scenario_id}", headers=theirs)).status_code == 404
        assert (
            await api.get(f"/v2/decisions/{decision['ref_id']}/scenarios", headers=theirs)
        ).status_code == 404
        assert (
            await api.post(
                "/v2/scenarios", json={"decision_id": decision["ref_id"]}, headers=theirs
            )
        ).status_code == 404
        assert (await api.get("/v2/scenarios", headers=theirs)).json()["total"] == 0
