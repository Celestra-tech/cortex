from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from cortex import AsyncCortex, Cortex, InternalServerError, ValidationError
from cortex.models import EvidenceParams

from .helpers import Server, json_response

NODE: dict[str, Any] = {
    "id": "n-1",
    "type": "decision",
    "ref_id": "c-1",
    "title": "Answer: How long do refunds take?",
    "confidence": 0.8,
    "created_at": "2026-09-27T12:00:00Z",
    "occurred_at": "2026-09-27T12:00:00Z",
    "metadata": {"model": "gpt-4.1"},
}
CHUNK = {**NODE, "id": "n-2", "type": "chunk", "ref_id": "k-1", "title": "Refund Policy · Refunds"}
EDGE: dict[str, Any] = {
    "id": "e-1",
    "type": "supports",
    "from_node_id": "n-2",
    "to_node_id": "n-1",
    "provenance": {
        "confidence": 0.9,
        "explanation": "Cited as [1] in the answer",
        "source": "cortex.knowledge.citations",
        "timestamp": "2026-09-27T12:00:00Z",
    },
    "created_at": "2026-09-27T12:00:00Z",
}
EVIDENCE: dict[str, Any] = {
    "decision": NODE,
    "supporting": [
        {"node": CHUNK, "depth": 1, "path_confidence": 0.9, "strength": 0.9, "path": ["e-1"]}
    ],
    "contradicting": [],
    "counts": {"chunk": 1},
}
GRAPH: dict[str, Any] = {
    "root_id": "n-1",
    "depth": 4,
    "nodes": [{**NODE, "depth": 0}, {**CHUNK, "depth": -1}],
    "edges": [EDGE],
    "timeline": [
        {
            "at": "2026-09-27T12:00:00Z",
            "kind": "edge",
            "id": "e-1",
            "label": "Refund Policy · Refunds supports Answer",
            "source": "cortex.knowledge.citations",
            "confidence": 0.9,
        }
    ],
    "truncated": False,
}


def test_reads(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response(EVIDENCE),
        json_response(GRAPH),
        json_response(
            {
                "node": CHUNK,
                "upstream": [],
                "downstream": [{"edge": EDGE, "node": NODE}],
                "decisions": [{"node": NODE, "depth": 1}],
            }
        ),
        json_response(
            {
                "source_id": "n-2",
                "target_id": "n-1",
                "connected": True,
                "edges": [EDGE],
                "nodes": [CHUNK, NODE],
            }
        ),
    )
    cortex = make_client(server)

    evidence = cortex.evidence.decision("c-1", depth=2)
    assert evidence.supporting[0].node.title == "Refund Policy · Refunds"
    assert server.requests[0].url.path == "/v2/evidence/c-1"
    assert server.requests[0].url.params["depth"] == "2"

    graph = cortex.evidence.graph("c-1")
    assert [n.depth for n in graph.nodes] == [0, -1]
    assert graph.edges[0].provenance.source == "cortex.knowledge.citations"
    assert server.requests[1].url.path == "/v2/evidence/c-1/graph"

    assert len(cortex.evidence.node("n-2").decisions) == 1
    assert server.requests[2].url.path == "/v2/evidence/node/n-2"

    assert cortex.evidence.path("n-2", "n-1", directed=True).connected
    assert dict(server.requests[3].url.params) == {
        "source": "n-2",
        "target": "n-1",
        "directed": "true",
    }


def test_iter_decisions(make_client: Callable[..., Cortex]) -> None:
    server = Server(
        json_response({"items": [NODE, NODE], "total": 3, "limit": 2, "offset": 0}),
        json_response({"items": [NODE], "total": 3, "limit": 2, "offset": 2}),
    )
    assert len(list(make_client(server).evidence.iter_decisions(page_size=2))) == 3
    assert server.requests[1].url.params["offset"] == "2"


def test_record_decision_is_validated_and_not_retried(make_client: Callable[..., Cortex]) -> None:
    server = Server(json_response({"detail": "boom"}, 500))
    cortex = make_client(server)
    evidence: list[EvidenceParams] = [
        {"type": "document", "title": "Refund Policy", "explanation": "In window"}
    ]

    with pytest.raises(ValidationError):
        cortex.evidence.record_decision(title="No evidence", evidence=[])
    with pytest.raises(ValidationError):
        cortex.evidence.record_decision(
            title="Bad",
            evidence=[{"type": "rumor", "title": "x", "explanation": "y"}],  # type: ignore[typeddict-item]
        )
    assert server.requests == []

    with pytest.raises(InternalServerError):
        cortex.evidence.record_decision(
            title="Approve refund",
            evidence=evidence,
            source="policy-engine@3",
        )
    assert len(server.requests) == 1
    assert server.body() == {
        "title": "Approve refund",
        "source": "policy-engine@3",
        "evidence": evidence,
    }


async def test_async(make_async_client: Callable[..., AsyncCortex]) -> None:
    server = Server(json_response(EVIDENCE, 201))
    cortex = make_async_client(server)
    recorded = await cortex.evidence.record_decision(
        title="Approve refund",
        evidence=[{"type": "memory", "title": "Loyal customer", "explanation": "Since 2019"}],
    )
    assert recorded.decision.ref_id == "c-1"
    assert server.last.method == "POST"
    assert server.last.url.path == "/v2/evidence/decisions"
