import pytest

from cortex_api.models.evidence_edge import EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNodeType
from cortex_api.services.evidence.traversal import (
    ancestors,
    contradicting_evidence,
    descendants,
    shortest_path,
    supporting_evidence,
)

from .builders import GraphBuilder

DERIVED = EvidenceEdgeType.DERIVED_FROM
RETRIEVED = EvidenceEdgeType.RETRIEVED_FROM
SUPPORTS = EvidenceEdgeType.SUPPORTS


@pytest.fixture
def refund() -> GraphBuilder:
    """A grounded answer, shaped like what a completion records.

    policy -> refunds-chunk -> retrieval -> answer -> reply
                    \\________________________^ (cited)
    memory ------------------------------------^
    faq -> chargebacks-chunk -> retrieval
    rumor --contradicts------------------------^
    """
    b = GraphBuilder()
    b.node("policy", EvidenceNodeType.DOCUMENT)
    b.node("refunds", EvidenceNodeType.CHUNK)
    b.node("faq", EvidenceNodeType.DOCUMENT)
    b.node("chargebacks", EvidenceNodeType.CHUNK)
    b.node("retrieval", EvidenceNodeType.KNOWLEDGE, confidence=0.8)
    b.node("memory", EvidenceNodeType.MEMORY, confidence=0.9)
    b.node("rumor", EvidenceNodeType.MEMORY)
    b.node("answer", EvidenceNodeType.DECISION)
    b.node("reply", EvidenceNodeType.MESSAGE)
    b.edge("policy", "refunds", DERIVED)
    b.edge("faq", "chargebacks", DERIVED)
    b.edge("refunds", "retrieval", RETRIEVED, confidence=1.0)
    b.edge("chargebacks", "retrieval", RETRIEVED, confidence=0.4)
    b.edge("retrieval", "answer", SUPPORTS, confidence=0.8)
    b.edge("refunds", "answer", SUPPORTS, confidence=0.9)
    b.edge("memory", "answer", SUPPORTS, confidence=0.5)
    b.edge("rumor", "answer", EvidenceEdgeType.CONTRADICTS, confidence=0.3)
    b.edge("answer", "reply", EvidenceEdgeType.GENERATED_BY)
    return b


class TestAncestorsAndDescendants:
    def test_ancestors_are_everything_upstream_nearest_first(self, refund: GraphBuilder) -> None:
        reached = ancestors(refund.graph(), refund.id("answer"))
        depths = {r.node.title: r.depth for r in reached}
        assert depths == {
            "retrieval": 1,
            "refunds": 1,
            "memory": 1,
            "rumor": 1,
            "policy": 2,
            "chargebacks": 2,
            "faq": 3,
        }
        assert [r.depth for r in reached] == sorted(r.depth for r in reached)

    def test_ancestors_respect_depth_and_edge_types(self, refund: GraphBuilder) -> None:
        graph = refund.graph()
        near = ancestors(graph, refund.id("answer"), max_depth=1)
        assert {r.node.title for r in near} == {"retrieval", "refunds", "memory", "rumor"}
        supports_only = ancestors(graph, refund.id("answer"), edge_types=[SUPPORTS])
        assert {r.node.title for r in supports_only} == {"retrieval", "refunds", "memory"}

    def test_descendants_are_everything_downstream(self, refund: GraphBuilder) -> None:
        reached = descendants(refund.graph(), refund.id("policy"))
        assert {r.node.title: r.depth for r in reached} == {
            "refunds": 1,
            "retrieval": 2,
            "answer": 2,
            "reply": 3,
        }

    def test_leaves_and_roots(self, refund: GraphBuilder) -> None:
        graph = refund.graph()
        assert ancestors(graph, refund.id("policy")) == []
        assert descendants(graph, refund.id("reply")) == []

    def test_cycles_terminate(self) -> None:
        b = GraphBuilder()
        for name in "abc":
            b.node(name)
        b.edge("a", "b")
        b.edge("b", "c")
        b.edge("c", "a")
        assert {r.node.title for r in descendants(b.graph(), b.id("a"))} == {"b", "c"}
        assert {r.node.title for r in ancestors(b.graph(), b.id("a"))} == {"b", "c"}


class TestShortestPath:
    def test_undirected_path_crosses_edge_directions(self, refund: GraphBuilder) -> None:
        path = shortest_path(refund.graph(), refund.id("faq"), refund.id("memory"))
        assert path is not None
        titles = [refund.graph().nodes[e.from_node_id].title for e in path]
        assert len(path) == 4  # faq -> chargebacks -> retrieval -> answer <- memory
        assert titles[:3] == ["faq", "chargebacks", "retrieval"]
        assert path[-1].from_node_id == refund.id("memory")

    def test_directed_path_only_flows_downstream(self, refund: GraphBuilder) -> None:
        graph = refund.graph()
        path = shortest_path(graph, refund.id("policy"), refund.id("reply"), directed=True)
        assert path is not None
        # policy -> refunds -> answer -> reply beats the route through retrieval.
        assert len(path) == 3
        assert shortest_path(graph, refund.id("reply"), refund.id("policy"), directed=True) is None

    def test_trivial_and_unknown(self, refund: GraphBuilder) -> None:
        graph = refund.graph()
        assert shortest_path(graph, refund.id("answer"), refund.id("answer")) == []
        stranger = GraphBuilder().node("stranger")
        assert shortest_path(graph, refund.id("answer"), stranger.id) is None

    def test_disconnected(self) -> None:
        b = GraphBuilder()
        b.node("a")
        b.node("b")
        assert shortest_path(b.graph(), b.id("a"), b.id("b")) is None


class TestSupportingEvidence:
    def test_ranks_by_strongest_route(self, refund: GraphBuilder) -> None:
        support = supporting_evidence(refund.graph(), refund.id("answer"))
        by_title = {s.node.title: s for s in support}

        assert "rumor" not in by_title
        assert "reply" not in by_title
        # Cited directly at 0.9 beats 1.0 * 0.8 through the retrieval.
        assert by_title["refunds"].path_confidence == pytest.approx(0.9)
        assert by_title["refunds"].depth == 1
        assert by_title["policy"].path_confidence == pytest.approx(0.9)
        assert by_title["policy"].depth == 2
        # Through the retrieval: its edge (0.8), the retrieval itself (0.8), then 0.4.
        assert by_title["chargebacks"].path_confidence == pytest.approx(0.8 * 0.8 * 0.4)
        assert by_title["faq"].depth == 3
        # Strength folds in the node's own confidence.
        assert by_title["memory"].strength == pytest.approx(0.5 * 0.9)
        assert by_title["retrieval"].strength == pytest.approx(0.8 * 0.8)

        strengths = [s.strength for s in support]
        assert strengths == sorted(strengths, reverse=True)

    def test_paths_lead_to_the_decision(self, refund: GraphBuilder) -> None:
        graph = refund.graph()
        for item in supporting_evidence(graph, refund.id("answer")):
            assert item.path[0].from_node_id == item.node.id
            assert item.path[-1].to_node_id == refund.id("answer")
            assert len(item.path) == item.depth
            for earlier, later in zip(item.path, item.path[1:], strict=False):
                assert earlier.to_node_id == later.from_node_id

    def test_max_depth(self, refund: GraphBuilder) -> None:
        support = supporting_evidence(refund.graph(), refund.id("answer"), max_depth=1)
        assert {s.node.title for s in support} == {"retrieval", "refunds", "memory"}

    def test_contradictions(self, refund: GraphBuilder) -> None:
        [contradiction] = contradicting_evidence(refund.graph(), refund.id("answer"))
        assert contradiction.node.title == "rumor"
        assert contradiction.edge.confidence == 0.3
