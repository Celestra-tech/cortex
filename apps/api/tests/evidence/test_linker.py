import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.evidence_edge import EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNodeType
from cortex_api.models.organization import Organization
from cortex_api.repositories.evidence_repository import EvidenceRepository, NodeValues
from cortex_api.services.evidence.graph import EvidenceGraphLoader, EvidenceNotFoundError
from cortex_api.services.evidence.linker import (
    DecisionExistsError,
    EvidenceLinker,
    EvidencePlan,
)
from cortex_api.services.evidence.provenance import Provenance
from cortex_api.services.evidence.traversal import EvidenceTraversal

pytestmark = pytest.mark.database


def values(type_: EvidenceNodeType, title: str, confidence: float = 1.0) -> NodeValues:
    return NodeValues(type=type_, ref_id=uuid7(), title=title, confidence=confidence)


def because(confidence: float, why: str = "because") -> Provenance:
    return Provenance.now(confidence, why, "test")


def test_plan_dedupes_nodes_and_links() -> None:
    plan = EvidencePlan()
    memory = values(EvidenceNodeType.MEMORY, "m")
    key = plan.add(memory)
    assert plan.add(memory) == key
    anonymous = NodeValues(type=EvidenceNodeType.BENCHMARK, title="eval", confidence=1.0)
    assert plan.add(anonymous) != plan.add(anonymous), "unreferenced nodes are always distinct"

    decision = plan.add(values(EvidenceNodeType.DECISION, "d"))
    plan.link(key, decision, EvidenceEdgeType.SUPPORTS, because(0.5, "first"))
    plan.link(key, decision, EvidenceEdgeType.SUPPORTS, because(0.9, "second"))
    assert len(plan.links) == 1
    assert plan.strengths(decision, EvidenceEdgeType.SUPPORTS) == [0.5]
    with pytest.raises(ValueError, match="itself"):
        plan.link(key, key, EvidenceEdgeType.SUPPORTS, because(1.0))


async def test_record_decision_derives_confidence_from_evidence(
    session: AsyncSession, organization: Organization
) -> None:
    linker = EvidenceLinker(session)
    plan = EvidencePlan()
    decision = plan.add(values(EvidenceNodeType.DECISION, "Approve the refund"))
    policy = plan.add(values(EvidenceNodeType.DOCUMENT, "Refund policy", confidence=0.9))
    memory = plan.add(values(EvidenceNodeType.MEMORY, "Customer since 2019"))
    rumor = plan.add(values(EvidenceNodeType.MEMORY, "Possible fraud flag"))
    plan.link(policy, decision, EvidenceEdgeType.SUPPORTS, because(1.0))
    plan.link(memory, decision, EvidenceEdgeType.SUPPORTS, because(0.5))
    plan.link(rumor, decision, EvidenceEdgeType.CONTRADICTS, because(0.2))

    recorded = await linker.record_decision(organization.id, plan, decision)

    # noisy-OR of 0.9 and 0.5 is 0.95, discounted by the 0.2 contradiction.
    assert recorded.decision.confidence == pytest.approx(0.95 * 0.8)
    assert recorded.decision.type is EvidenceNodeType.DECISION
    assert len(recorded.nodes) == 4
    assert len(recorded.edges) == 3
    assert all(e.to_node_id == recorded.decision.id for e in recorded.edges)
    assert {e.source for e in recorded.edges} == {"test"}

    with pytest.raises(DecisionExistsError):
        await linker.record_decision(organization.id, plan, decision)


async def test_record_decision_honours_explicit_confidence(
    session: AsyncSession, organization: Organization
) -> None:
    plan = EvidencePlan()
    decision = plan.add(values(EvidenceNodeType.DECISION, "Ship it"))
    recorded = await EvidenceLinker(session).record_decision(
        organization.id, plan, decision, confidence=0.42
    )
    assert recorded.decision.confidence == 0.42
    assert recorded.edges == []


async def test_record_decision_requires_a_decision_node(
    session: AsyncSession, organization: Organization
) -> None:
    plan = EvidencePlan()
    memory = plan.add(values(EvidenceNodeType.MEMORY, "m"))
    with pytest.raises(ValueError, match="decision"):
        await EvidenceLinker(session).record_decision(organization.id, plan, memory)


async def test_shared_evidence_links_many_decisions(
    session: AsyncSession, organization: Organization
) -> None:
    """Evidence is a graph, not a tree: one document can back many decisions."""
    linker = EvidenceLinker(session)
    policy = values(EvidenceNodeType.DOCUMENT, "Refund policy")
    decisions = []
    for title in ("Refund A", "Refund B"):
        plan = EvidencePlan()
        decision = plan.add(values(EvidenceNodeType.DECISION, title))
        plan.link(plan.add(policy), decision, EvidenceEdgeType.SUPPORTS, because(1.0))
        decisions.append((await linker.record_decision(organization.id, plan, decision)).decision)

    traversal = EvidenceTraversal(EvidenceGraphLoader(EvidenceRepository(session)))
    document = await EvidenceRepository(session).get_by_ref(
        organization.id,
        EvidenceNodeType.DOCUMENT,
        policy.ref_id,  # type: ignore[arg-type]
    )
    assert document is not None
    reached = await traversal.descendants(organization.id, document.id)
    assert {r.node.id for r in reached} == {d.id for d in decisions}

    path = await traversal.shortest_path(organization.id, decisions[0].id, decisions[1].id)
    assert path is not None and len(path) == 2
    assert (
        await traversal.shortest_path(
            organization.id, decisions[0].id, decisions[1].id, directed=True
        )
        is None
    )


async def test_traversal_over_stored_graph(
    session: AsyncSession, organization: Organization
) -> None:
    linker = EvidenceLinker(session)
    plan = EvidencePlan()
    decision_values = values(EvidenceNodeType.DECISION, "Answer")
    decision = plan.add(decision_values)
    document = plan.add(values(EvidenceNodeType.DOCUMENT, "Policy"))
    chunk = plan.add(values(EvidenceNodeType.CHUNK, "Policy · Refunds"))
    knowledge = plan.add(values(EvidenceNodeType.KNOWLEDGE, "Retrieval", confidence=0.7))
    plan.link(document, chunk, EvidenceEdgeType.DERIVED_FROM, because(1.0))
    plan.link(chunk, knowledge, EvidenceEdgeType.RETRIEVED_FROM, because(0.8))
    plan.link(knowledge, decision, EvidenceEdgeType.SUPPORTS, because(0.7))
    recorded = await linker.record_decision(organization.id, plan, decision)

    traversal = EvidenceTraversal(EvidenceGraphLoader(EvidenceRepository(session)))
    ancestors = await traversal.ancestors(organization.id, recorded.decision.id)
    assert [(r.node.title, r.depth) for r in ancestors] == [
        ("Retrieval", 1),
        ("Policy · Refunds", 2),
        ("Policy", 3),
    ]
    assert [
        r.node.title
        for r in await traversal.ancestors(organization.id, recorded.decision.id, max_depth=1)
    ] == ["Retrieval"]

    support = await traversal.supporting_evidence(organization.id, decision_values.ref_id)  # type: ignore[arg-type]
    assert [s.node.title for s in support][:1] == ["Retrieval"]
    by_title = {s.node.title: s for s in support}
    # knowledge edge 0.7, knowledge node 0.7, retrieval edge 0.8, chunk node 1.0, ingestion 1.0
    assert by_title["Policy"].path_confidence == pytest.approx(0.7 * 0.7 * 0.8)

    with pytest.raises(EvidenceNotFoundError):
        await traversal.supporting_evidence(organization.id, uuid7())
    with pytest.raises(EvidenceNotFoundError):
        await traversal.ancestors(organization.id, uuid7())


async def test_link_between_stored_nodes(session: AsyncSession, organization: Organization) -> None:
    linker = EvidenceLinker(session)
    nodes = await linker.repository.upsert_nodes(
        organization.id,
        [values(EvidenceNodeType.MEMORY, "m"), values(EvidenceNodeType.DECISION, "d")],
    )
    edge = await linker.link(
        organization.id, nodes[0], nodes[1], EvidenceEdgeType.SUPPORTS, because(0.3)
    )
    assert (edge.confidence, edge.explanation, edge.source) == (0.3, "because", "test")
    with pytest.raises(ValueError, match="itself"):
        await linker.link(
            organization.id, nodes[0], nodes[0], EvidenceEdgeType.SUPPORTS, because(1.0)
        )
