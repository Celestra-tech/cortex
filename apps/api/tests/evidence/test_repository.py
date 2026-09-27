from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.database.ids import uuid7
from cortex_api.models.evidence_edge import EvidenceEdge, EvidenceEdgeType
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.models.organization import Organization
from cortex_api.repositories.evidence_repository import (
    Direction,
    EdgeValues,
    EvidenceRepository,
    NodeValues,
)

from ..conftest import OrganizationFactory

pytestmark = pytest.mark.database

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def node(type_: EvidenceNodeType, title: str, **kwargs: object) -> NodeValues:
    return NodeValues(type=type_, title=title, confidence=1.0, ref_id=uuid7(), **kwargs)  # type: ignore[arg-type]


def edge(
    upstream: EvidenceNode,
    downstream: EvidenceNode,
    type_: EvidenceEdgeType = EvidenceEdgeType.SUPPORTS,
    confidence: float = 1.0,
    explanation: str = "because",
    at: datetime = NOW,
) -> EdgeValues:
    return EdgeValues(
        from_node_id=upstream.id,
        to_node_id=downstream.id,
        type=type_,
        confidence=confidence,
        explanation=explanation,
        source="test",
        observed_at=at,
    )


async def test_upsert_nodes_dedupes_references_and_keeps_order(
    session: AsyncSession, organization: Organization
) -> None:
    repository = EvidenceRepository(session)
    memory = node(EvidenceNodeType.MEMORY, "Billing runs monthly")
    anonymous = NodeValues(type=EvidenceNodeType.BENCHMARK, title="Offline eval", confidence=0.7)

    first = await repository.upsert_nodes(organization.id, [memory, anonymous, memory])
    assert first[0] is first[2]
    assert first[0].id.version == 7
    assert first[1].ref_id is None
    assert first[1].confidence == 0.7

    renamed = NodeValues(
        type=memory.type, ref_id=memory.ref_id, title="A later title", confidence=0.1
    )
    [again] = await repository.upsert_nodes(organization.id, [renamed])
    assert again.id == first[0].id
    assert again.title == "Billing runs monthly", "nodes are snapshots; the first write wins"

    assert await repository.get_by_ref(organization.id, memory.type, memory.ref_id) is again  # type: ignore[arg-type]


async def test_upsert_edges_is_idempotent_and_keeps_first_provenance(
    session: AsyncSession, organization: Organization
) -> None:
    repository = EvidenceRepository(session)
    memory, decision = await repository.upsert_nodes(
        organization.id,
        [node(EvidenceNodeType.MEMORY, "m"), node(EvidenceNodeType.DECISION, "d")],
    )
    [created] = await repository.upsert_edges(
        organization.id, [edge(memory, decision, confidence=0.6, explanation="first")]
    )
    assert created.observed_at == NOW
    [repeat, other] = await repository.upsert_edges(
        organization.id,
        [
            edge(memory, decision, confidence=0.1, explanation="second"),
            edge(memory, decision, EvidenceEdgeType.CONTRADICTS, confidence=0.2),
        ],
    )
    assert repeat.id == created.id
    assert (repeat.confidence, repeat.explanation) == (0.6, "first")
    assert other.type is EvidenceEdgeType.CONTRADICTS
    count = len((await session.scalars(select(EvidenceEdge))).all())
    assert count == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1.5},
        {"confidence": -0.5},
        {"explanation": "  "},
        {"source": ""},
    ],
)
async def test_edge_constraints(
    session: AsyncSession, organization: Organization, bad: dict[str, object]
) -> None:
    repository = EvidenceRepository(session)
    a, b = await repository.upsert_nodes(
        organization.id, [node(EvidenceNodeType.MEMORY, "a"), node(EvidenceNodeType.DECISION, "b")]
    )
    invalid = replace(edge(a, b), **bad)
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repository.upsert_edges(organization.id, [invalid])


async def test_node_constraints(session: AsyncSession, organization: Organization) -> None:
    repository = EvidenceRepository(session)
    [a] = await repository.upsert_nodes(organization.id, [node(EvidenceNodeType.MEMORY, "a")])
    for invalid in (
        NodeValues(type=EvidenceNodeType.DECISION, title="no ref", confidence=1.0),
        NodeValues(type=EvidenceNodeType.MEMORY, title="   ", confidence=1.0),
        NodeValues(type=EvidenceNodeType.MEMORY, title="x", confidence=2.0),
    ):
        with pytest.raises(IntegrityError):
            async with session.begin_nested():
                await repository.upsert_nodes(organization.id, [invalid])
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await repository.upsert_edges(organization.id, [edge(a, a)])


async def test_deleting_a_node_removes_its_edges(
    session: AsyncSession, organization: Organization
) -> None:
    repository = EvidenceRepository(session)
    a, b = await repository.upsert_nodes(
        organization.id, [node(EvidenceNodeType.MEMORY, "a"), node(EvidenceNodeType.DECISION, "b")]
    )
    await repository.upsert_edges(organization.id, [edge(a, b)])
    await session.delete(a)
    await session.flush()
    assert (await session.scalars(select(EvidenceEdge))).all() == []


@pytest.fixture
async def chain(session: AsyncSession, organization: Organization) -> dict[str, EvidenceNode]:
    """document -> chunk -> knowledge -> decision -> reply, plus a contradiction."""
    repository = EvidenceRepository(session)
    names = ["document", "chunk", "knowledge", "decision", "reply", "rumor"]
    types = [
        EvidenceNodeType.DOCUMENT,
        EvidenceNodeType.CHUNK,
        EvidenceNodeType.KNOWLEDGE,
        EvidenceNodeType.DECISION,
        EvidenceNodeType.MESSAGE,
        EvidenceNodeType.MEMORY,
    ]
    nodes = await repository.upsert_nodes(
        organization.id, [node(t, n) for n, t in zip(names, types, strict=True)]
    )
    by_name = dict(zip(names, nodes, strict=True))
    await repository.upsert_edges(
        organization.id,
        [
            edge(by_name["document"], by_name["chunk"], EvidenceEdgeType.DERIVED_FROM),
            edge(by_name["chunk"], by_name["knowledge"], EvidenceEdgeType.RETRIEVED_FROM),
            edge(by_name["knowledge"], by_name["decision"]),
            edge(by_name["decision"], by_name["reply"], EvidenceEdgeType.GENERATED_BY),
            edge(by_name["rumor"], by_name["decision"], EvidenceEdgeType.CONTRADICTS),
        ],
    )
    return by_name


async def test_walk_upstream_downstream_and_both(
    session: AsyncSession, organization: Organization, chain: dict[str, EvidenceNode]
) -> None:
    repository = EvidenceRepository(session)
    names = {n.id: name for name, n in chain.items()}

    def ends(edges: list[EvidenceEdge]) -> set[tuple[str, str]]:
        return {(names[e.from_node_id], names[e.to_node_id]) for e in edges}

    upstream = await repository.walk(
        organization.id,
        chain["decision"].id,
        direction=Direction.UPSTREAM,
        max_depth=10,
        max_edges=100,
    )
    assert ends(list(upstream)) == {
        ("document", "chunk"),
        ("chunk", "knowledge"),
        ("knowledge", "decision"),
        ("rumor", "decision"),
    }
    shallow = await repository.walk(
        organization.id,
        chain["decision"].id,
        direction=Direction.UPSTREAM,
        max_depth=1,
        max_edges=100,
    )
    assert ends(list(shallow)) == {("knowledge", "decision"), ("rumor", "decision")}
    supports = await repository.walk(
        organization.id,
        chain["decision"].id,
        direction=Direction.UPSTREAM,
        max_depth=10,
        max_edges=100,
        edge_types=[EvidenceEdgeType.SUPPORTS, EvidenceEdgeType.RETRIEVED_FROM],
    )
    assert ends(list(supports)) == {("chunk", "knowledge"), ("knowledge", "decision")}

    downstream = await repository.walk(
        organization.id,
        chain["chunk"].id,
        direction=Direction.DOWNSTREAM,
        max_depth=10,
        max_edges=100,
    )
    assert ends(list(downstream)) == {
        ("chunk", "knowledge"),
        ("knowledge", "decision"),
        ("decision", "reply"),
    }

    both = await repository.walk(
        organization.id, chain["knowledge"].id, direction=Direction.BOTH, max_depth=1, max_edges=100
    )
    assert ends(list(both)) == {("chunk", "knowledge"), ("knowledge", "decision")}
    everything = await repository.walk(
        organization.id, chain["reply"].id, direction=Direction.BOTH, max_depth=10, max_edges=100
    )
    assert len(everything) == 5

    budget = await repository.walk(
        organization.id,
        chain["decision"].id,
        direction=Direction.UPSTREAM,
        max_depth=10,
        max_edges=2,
    )
    assert ends(list(budget)) == {("knowledge", "decision"), ("rumor", "decision")}, "nearest first"


async def test_walk_terminates_on_cycles(session: AsyncSession, organization: Organization) -> None:
    repository = EvidenceRepository(session)
    a, b, c = await repository.upsert_nodes(
        organization.id, [node(EvidenceNodeType.MEMORY, name) for name in "abc"]
    )
    await repository.upsert_edges(organization.id, [edge(a, b), edge(b, c), edge(c, a)])
    edges = await repository.walk(
        organization.id, a.id, direction=Direction.DOWNSTREAM, max_depth=10, max_edges=100
    )
    assert len(edges) == 3


async def test_evidence_is_scoped_to_its_organization(
    session: AsyncSession,
    organization: Organization,
    organization_factory: OrganizationFactory,
    chain: dict[str, EvidenceNode],
) -> None:
    repository = EvidenceRepository(session)
    other = await organization_factory()
    decision = chain["decision"]

    assert await repository.get_node(other.id, decision.id) is None
    assert await repository.get_by_ref(other.id, decision.type, decision.ref_id) is None  # type: ignore[arg-type]
    assert await repository.get_nodes(other.id, [decision.id]) == {}
    assert (
        await repository.walk(
            other.id, decision.id, direction=Direction.BOTH, max_depth=10, max_edges=100
        )
        == []
    )
    assert await repository.edges_touching(other.id, [decision.id]) == []
    assert await repository.count_decisions(other.id) == 0

    # The same record can be evidence in two tenants without colliding.
    [theirs] = await repository.upsert_nodes(
        other.id,
        [NodeValues(type=decision.type, ref_id=decision.ref_id, title="theirs", confidence=1.0)],
    )
    assert theirs.id != decision.id


async def test_list_and_count_decisions(session: AsyncSession, organization: Organization) -> None:
    repository = EvidenceRepository(session)
    decisions = await repository.upsert_nodes(
        organization.id, [node(EvidenceNodeType.DECISION, f"d{i}") for i in range(3)]
    )
    await repository.upsert_nodes(organization.id, [node(EvidenceNodeType.MEMORY, "m")])
    # Timestamps within one transaction tie; ids (UUIDv7) break the tie newest first.
    listed = await repository.list_decisions(organization.id, limit=2, offset=0)
    assert [d.id for d in listed] == [decisions[2].id, decisions[1].id]
    assert await repository.count_decisions(organization.id) == 3
    assert [d.id for d in await repository.list_decisions(organization.id, limit=5, offset=2)] == [
        decisions[0].id
    ]


async def test_edges_touching(
    session: AsyncSession, organization: Organization, chain: dict[str, EvidenceNode]
) -> None:
    repository = EvidenceRepository(session)
    decision = chain["decision"]
    assert len(await repository.edges_touching(organization.id, [decision.id])) == 3
    upstream = await repository.edges_touching(
        organization.id, [decision.id], direction=Direction.UPSTREAM
    )
    assert {e.to_node_id for e in upstream} == {decision.id}
    downstream = await repository.edges_touching(
        organization.id, [decision.id], direction=Direction.DOWNSTREAM
    )
    assert [e.to_node_id for e in downstream] == [chain["reply"].id]
    assert len(await repository.edges_touching(organization.id, [decision.id], limit=1)) == 1
    assert await repository.edges_touching(organization.id, []) == []
