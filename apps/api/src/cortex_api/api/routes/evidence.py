"""The Evidence Graph: why Cortex decided what it decided."""

import uuid
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from cortex_api.api.deps import OrganizationDep, PrincipalDep
from cortex_api.database.ids import uuid7
from cortex_api.database.session import DbSession
from cortex_api.models.evidence_node import EvidenceNode, EvidenceNodeType
from cortex_api.repositories.evidence_repository import (
    Direction,
    EvidenceRepository,
    NodeValues,
)
from cortex_api.schemas.evidence import (
    ContradictionRead,
    DecisionCreate,
    DecisionEvidenceResponse,
    DecisionListResponse,
    EvidenceEdgeRead,
    EvidenceGraphResponse,
    EvidenceNodeDetailResponse,
    EvidenceNodeRead,
    EvidencePathResponse,
    GraphNodeRead,
    NeighborRead,
    ReachedRead,
    SupportingEvidenceRead,
    TimelineEventRead,
)
from cortex_api.services.evidence.graph import (
    DEFAULT_DEPTH,
    MAX_DEPTH,
    EvidenceGraph,
    EvidenceGraphLoader,
)
from cortex_api.services.evidence.linker import EvidenceLinker, EvidencePlan
from cortex_api.services.evidence.provenance import (
    Provenance,
    ProvenanceError,
    api_source,
    timeline,
)
from cortex_api.services.evidence.traversal import (
    EvidenceTraversal,
    ancestors,
    contradicting_evidence,
    descendants,
    supporting_evidence,
)

router = APIRouter(prefix="/evidence", tags=["evidence"])

MAX_NEIGHBORS = 500

Depth = Annotated[int, Query(ge=1, le=MAX_DEPTH, description="Hops to follow from the root.")]


def get_loader(session: DbSession) -> EvidenceGraphLoader:
    return EvidenceGraphLoader(EvidenceRepository(session))


LoaderDep = Annotated[EvidenceGraphLoader, Depends(get_loader)]


@router.get("", response_model=DecisionListResponse)
async def list_decisions(
    organization: OrganizationDep,
    loader: LoaderDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DecisionListResponse:
    """Recorded decisions, newest first."""
    repository = loader.repository
    decisions = await repository.list_decisions(organization.id, limit=limit, offset=offset)
    return DecisionListResponse(
        items=[EvidenceNodeRead.of(node) for node in decisions],
        total=await repository.count_decisions(organization.id),
        limit=limit,
        offset=offset,
    )


@router.post(
    "/decisions",
    response_model=DecisionEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "A decision with this ref_id is already recorded."}},
)
async def record_decision(
    body: DecisionCreate, principal: PrincipalDep, session: DbSession, loader: LoaderDep
) -> DecisionEvidenceResponse:
    """Records a decision made outside Cortex, with the evidence behind it.

    Completions are recorded automatically; this is for decisions your own
    systems make, so they are traceable in the same graph.
    """
    organization_id = principal.organization.id
    source = api_source(principal.api_key_id)
    if body.source:
        source = f"{body.source} via {source}"
    ref_id = body.ref_id or uuid7()
    now = datetime.now(UTC)

    plan = EvidencePlan()
    decision = plan.add(
        NodeValues(
            type=EvidenceNodeType.DECISION,
            ref_id=ref_id,
            title=body.title,
            confidence=0.0,
            metadata={**body.metadata, "kind": "external", "occurred_at": now.isoformat()},
        )
    )
    for item in body.evidence:
        if item.type is EvidenceNodeType.DECISION and item.ref_id is None:
            raise ProvenanceError("decision evidence needs the ref_id of that decision")
        if item.type is EvidenceNodeType.DECISION and item.ref_id == ref_id:
            raise ProvenanceError("a decision cannot be evidence for itself")
        key = plan.add(
            NodeValues(
                type=item.type,
                ref_id=item.ref_id,
                title=item.title,
                confidence=item.confidence,
                metadata=item.metadata,
            )
        )
        observed = item.observed_at or now
        plan.link(
            key,
            decision,
            item.relation,
            Provenance(
                item.relation_confidence,
                item.explanation,
                source,
                observed if observed.tzinfo else observed.replace(tzinfo=UTC),
            ),
        )
    recorded = await EvidenceLinker(session).record_decision(
        organization_id, plan, decision, confidence=body.confidence
    )
    await session.commit()
    return await _decision_evidence(loader, organization_id, recorded.decision, DEFAULT_DEPTH)


@router.get("/node/{node_id}", response_model=EvidenceNodeDetailResponse)
async def get_node(
    node_id: uuid.UUID,
    organization: OrganizationDep,
    loader: LoaderDep,
    depth: Depth = DEFAULT_DEPTH,
) -> EvidenceNodeDetailResponse:
    """One node, its direct neighbors with provenance, and the decisions it fed."""
    node = await loader.node(organization.id, node_id)
    edges = await loader.repository.edges_touching(organization.id, [node.id], limit=MAX_NEIGHBORS)
    neighbors = await loader.repository.get_nodes(
        organization.id,
        ({e.from_node_id for e in edges} | {e.to_node_id for e in edges}) - {node.id},
    )
    downstream = await loader.around(
        organization.id, node, direction=Direction.DOWNSTREAM, depth=depth
    )
    decisions = [
        reached
        for reached in descendants(downstream, node.id)
        if reached.node.type is EvidenceNodeType.DECISION
    ]
    return EvidenceNodeDetailResponse(
        node=EvidenceNodeRead.of(node),
        upstream=[
            NeighborRead(
                edge=EvidenceEdgeRead.of(e), node=EvidenceNodeRead.of(neighbors[e.from_node_id])
            )
            for e in edges
            if e.to_node_id == node.id and e.from_node_id in neighbors
        ],
        downstream=[
            NeighborRead(
                edge=EvidenceEdgeRead.of(e), node=EvidenceNodeRead.of(neighbors[e.to_node_id])
            )
            for e in edges
            if e.from_node_id == node.id and e.to_node_id in neighbors
        ],
        decisions=[ReachedRead.of(r) for r in decisions],
    )


@router.get("/path", response_model=EvidencePathResponse)
async def get_path(
    organization: OrganizationDep,
    loader: LoaderDep,
    source: Annotated[uuid.UUID, Query(description="Node id to start from.")],
    target: Annotated[uuid.UUID, Query(description="Node id to reach.")],
    directed: Annotated[
        bool, Query(description="Only follow edges downstream, from evidence to decision.")
    ] = False,
    depth: Depth = MAX_DEPTH,
) -> EvidencePathResponse:
    """The shortest chain of evidence connecting two nodes."""
    path = await EvidenceTraversal(loader).shortest_path(
        organization.id, source, target, max_depth=depth, directed=directed
    )
    edges = path or []
    order = [source]
    for edge in edges:
        order.append(edge.from_node_id if edge.to_node_id == order[-1] else edge.to_node_id)
    nodes = await loader.repository.get_nodes(organization.id, set(order))
    return EvidencePathResponse(
        source_id=source,
        target_id=target,
        connected=path is not None,
        edges=[EvidenceEdgeRead.of(e) for e in edges],
        nodes=[EvidenceNodeRead.of(nodes[n]) for n in order if n in nodes]
        if path is not None
        else [],
    )


@router.get("/{decision_id}", response_model=DecisionEvidenceResponse)
async def get_decision_evidence(
    decision_id: uuid.UUID,
    organization: OrganizationDep,
    loader: LoaderDep,
    depth: Depth = DEFAULT_DEPTH,
) -> DecisionEvidenceResponse:
    """A decision and the evidence behind it, strongest first.

    `decision_id` is the id of what was decided: for completions, the
    completion id; for recorded decisions, their `ref_id`.
    """
    decision = await loader.decision(organization.id, decision_id)
    return await _decision_evidence(loader, organization.id, decision, depth)


@router.get("/{decision_id}/graph", response_model=EvidenceGraphResponse)
async def get_decision_graph(
    decision_id: uuid.UUID,
    organization: OrganizationDep,
    loader: LoaderDep,
    depth: Depth = DEFAULT_DEPTH,
) -> EvidenceGraphResponse:
    """The decision's evidence graph, laid out by distance, with a provenance timeline."""
    decision = await loader.decision(organization.id, decision_id)
    graph = await loader.decision_graph(organization.id, decision, depth=depth)
    return EvidenceGraphResponse(
        root_id=decision.id,
        depth=depth,
        nodes=_graph_nodes(graph, decision),
        edges=[EvidenceEdgeRead.of(e) for e in graph.edges.values()],
        timeline=[
            TimelineEventRead.of(e)
            for e in timeline(graph.nodes.values(), list(graph.edges.values()))
        ],
        truncated=graph.truncated,
    )


async def _decision_evidence(
    loader: EvidenceGraphLoader, organization_id: uuid.UUID, decision: EvidenceNode, depth: int
) -> DecisionEvidenceResponse:
    graph = await loader.around(
        organization_id, decision, direction=Direction.UPSTREAM, depth=depth
    )
    supporting = supporting_evidence(graph, decision.id)
    return DecisionEvidenceResponse(
        decision=EvidenceNodeRead.of(decision),
        supporting=[SupportingEvidenceRead.of(s) for s in supporting],
        contradicting=[ContradictionRead.of(c) for c in contradicting_evidence(graph, decision.id)],
        counts=dict(Counter(s.node.type for s in supporting)),
    )


def _graph_nodes(graph: EvidenceGraph, root: EvidenceNode) -> list[GraphNodeRead]:
    depth = {root.id: 0}
    depth.update({r.node.id: -r.depth for r in ancestors(graph, root.id)})
    for reached in descendants(graph, root.id):
        depth.setdefault(reached.node.id, reached.depth)
    return [
        GraphNodeRead(**EvidenceNodeRead.of(node).model_dump(), depth=depth.get(node.id, 0))
        for node in sorted(graph.nodes.values(), key=lambda n: (depth.get(n.id, 0), n.created_at))
    ]
