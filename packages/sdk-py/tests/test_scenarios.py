from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from cortex import AsyncCortex, Cortex, InternalServerError, ValidationError

from .helpers import Server, json_response

CRITERION = {"value": 0.6, "desirability": 0.6, "weight": 0.2, "contribution": 0.12}
SCENARIO: dict[str, Any] = {
    "id": "s-1",
    "simulation_id": "sim-1",
    "decision_id": "c-1",
    "type": "best_case",
    "name": "Best case",
    "description": "Evidence holds up and execution is strong.",
    "objective": "Keep the customer",
    "score": 0.77,
    "confidence": 0.4,
    "rank": 1,
    "success_likelihood": 0.82,
    "expected_impact": 0.5,
    "criteria": {
        name: CRITERION
        for name in (
            "evidence_quality",
            "uncertainty",
            "constraint_satisfaction",
            "objective_alignment",
            "risk_exposure",
        )
    },
    "evidence": {"relied": 2, "excluded": 0, "contradictions": 1, "truncated": False},
    "strategy": {
        "optimism": 0.5,
        "contradiction_realization": 0.5,
        "evidence_floor": 0,
        "commitment": 0.8,
        "adherence_hard": 0.97,
        "adherence_soft": 0.9,
    },
    "assumptions": [
        {
            "id": "a-1",
            "kind": "evidence",
            "statement": "Refund Policy holds",
            "confidence": 0.9,
            "baseline_confidence": 0.8,
            "source": "cortex.evidence",
            "evidence_node_id": "n-2",
        }
    ],
    "outcomes": [
        {
            "id": "o-1",
            "kind": "objective_met",
            "result": "The objective is met",
            "impact": 0.8,
            "likelihood": 0.82,
            "expected_impact": 0.656,
            "assumption_id": None,
        }
    ],
    "created_at": "2026-09-27T12:00:00Z",
}
SIMULATION: dict[str, Any] = {
    "simulation_id": "sim-1",
    "decision_id": "c-1",
    "objective": "Keep the customer",
    "parameters": {"risk_tolerance": 0.5},
    "recommended_id": "s-1",
    "scenarios": [SCENARIO],
    "created_at": "2026-09-27T12:00:00Z",
}
SUMMARY: dict[str, Any] = {
    "simulation_id": "sim-1",
    "decision_id": "c-1",
    "decision_title": "Approve refund",
    "objective": "Keep the customer",
    "scenario_count": 5,
    "recommended": {
        "id": "s-1",
        "type": "best_case",
        "name": "Best case",
        "score": 0.77,
        "confidence": 0.4,
    },
    "created_at": "2026-09-27T12:00:00Z",
}


def test_reads(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response(SCENARIO),
        json_response(
            {"decision_id": "c-1", "simulations": [SIMULATION], "total": 1, "limit": 2, "offset": 0}
        ),
    )
    cortex = make_client(server)

    scenario = cortex.scenarios.get("s-1")
    assert scenario.criteria["risk_exposure"].contribution == 0.12
    assert scenario.assumptions[0].evidence_node_id == "n-2"
    assert scenario.strategy.commitment == 0.8
    assert server.requests[0].url.path == "/v2/scenarios/s-1"

    history = cortex.scenarios.for_decision("c-1", limit=2)
    assert history.simulations[0].recommended.name == "Best case"
    assert server.requests[1].url.path == "/v2/decisions/c-1/scenarios"
    assert dict(server.requests[1].url.params) == {"limit": "2"}


def test_iter(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({"items": [SUMMARY, SUMMARY], "total": 3, "limit": 2, "offset": 0}),
        json_response({"items": [SUMMARY], "total": 3, "limit": 2, "offset": 2}),
    )
    summaries = list(make_client(server).scenarios.iter(page_size=2))
    assert [s.recommended.type for s in summaries] == ["best_case"] * 3
    assert server.requests[1].url.params["offset"] == "2"


def test_simulate_is_validated_and_not_retried(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "boom"}, 500))
    cortex = make_client(server)

    invalid: list[dict[str, Any]] = [
        {"types": ["base_case", "base_case"]},
        {"types": []},
        {"risk_tolerance": 1.5},
        {"weights": {"uncertainty": -1}},
        {"assumptions": [{"statement": "", "confidence": 0.5}]},
        {"depth": 0},
    ]
    for bad in invalid:
        with pytest.raises(ValidationError):
            cortex.scenarios.simulate("c-1", **bad)
    assert server.requests == []

    with pytest.raises(InternalServerError):
        cortex.scenarios.simulate(
            "c-1",
            objective="Keep the customer",
            constraints=[{"statement": "Refund under $500", "severity": "hard"}],
            weights={"risk_exposure": 0.4},
        )
    assert len(server.requests) == 1
    assert server.body() == {
        "decision_id": "c-1",
        "objective": "Keep the customer",
        "constraints": [{"statement": "Refund under $500", "severity": "hard"}],
        "weights": {"risk_exposure": 0.4},
    }


async def test_async(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(SIMULATION, 201))
    cortex = make_async_client(server)
    simulation = await cortex.scenarios.simulate(
        "c-1",
        assumptions=[{"statement": "Finance approves", "confidence": 0.9}],
        types=["best_case"],
        risk_tolerance=0.2,
    )
    assert simulation.recommended.id == "s-1"
    assert server.last.method == "POST"
    assert server.last.url.path == "/v2/scenarios"
    assert server.body()["types"] == ["best_case"]
