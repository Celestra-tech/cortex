from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from ._resource import AsyncAPIResource, SyncAPIResource, apaginate, paginate, segment
from ._transport import Call, RequestOptions
from ._validation import validate_params
from .models import (
    DecisionEvidence,
    DecisionParams,
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeDetail,
    EvidenceParams,
    EvidencePath,
    Page,
)

DecisionPage = Page[EvidenceNode]


def _list(limit: int | None, offset: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="evidence.list_decisions",
        path="/v2/evidence",
        params={"limit": limit, "offset": offset},
        options=options,
    )


def _decision(id: str, depth: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="evidence.decision",
        path=f"/v2/evidence/{segment(id)}",
        params={"depth": depth},
        options=options,
    )


def _graph(id: str, depth: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="evidence.graph",
        path=f"/v2/evidence/{segment(id)}/graph",
        params={"depth": depth},
        options=options,
    )


def _node(id: str, depth: int | None, options: RequestOptions | None) -> Call:
    return Call(
        operation="evidence.node",
        path=f"/v2/evidence/node/{segment(id)}",
        params={"depth": depth},
        options=options,
    )


def _path(
    source: str,
    target: str,
    directed: bool | None,
    depth: int | None,
    options: RequestOptions | None,
) -> Call:
    return Call(
        operation="evidence.path",
        path="/v2/evidence/path",
        params={"source": source, "target": target, "directed": directed, "depth": depth},
        options=options,
    )


def _record(
    title: str,
    evidence: Sequence[EvidenceParams],
    ref_id: str | None,
    confidence: float | None,
    metadata: dict[str, Any] | None,
    source: str | None,
    options: RequestOptions | None,
) -> Call:
    op = "evidence.record_decision"
    params: dict[str, Any] = {"title": title, "evidence": list(evidence)}
    for key, value in (
        ("ref_id", ref_id),
        ("confidence", confidence),
        ("metadata", metadata),
        ("source", source),
    ):
        if value is not None:
            params[key] = value
    body = validate_params(op, DecisionParams, params)
    # A repeat with the same ref_id is a conflict, and without one a duplicate.
    return Call(
        operation=op,
        method="POST",
        path="/v2/evidence/decisions",
        json=body,
        options={"max_retries": 0, **(options or {})},
    )


class Evidence(SyncAPIResource):
    """The Evidence Graph: every decision linked to the records that produced it.

    A decision is addressed by the id of what was decided. For completions that
    is the completion id, so `evidence.decision(completion.id)` explains any answer.
    """

    def list_decisions(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionPage:
        """Recorded decisions, newest first."""
        return self._transport.request(_list(limit, offset, request_options), DecisionPage)

    def iter_decisions(self, *, page_size: int = 100) -> Iterator[EvidenceNode]:
        return paginate(
            lambda limit, offset: self.list_decisions(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    def decision(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> DecisionEvidence:
        """A decision and its evidence, strongest first, with contradictions."""
        return self._transport.request(_decision(id, depth, request_options), DecisionEvidence)

    def graph(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> EvidenceGraph:
        """The decision's graph with signed depths and a provenance timeline."""
        return self._transport.request(_graph(id, depth, request_options), EvidenceGraph)

    def node(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> EvidenceNodeDetail:
        """One node by node id, its direct neighbors, and the decisions it fed."""
        return self._transport.request(_node(id, depth, request_options), EvidenceNodeDetail)

    def path(
        self,
        source: str,
        target: str,
        *,
        directed: bool | None = None,
        depth: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> EvidencePath:
        """The shortest chain of evidence between two nodes."""
        call = _path(source, target, directed, depth, request_options)
        return self._transport.request(call, EvidencePath)

    def record_decision(
        self,
        *,
        title: str,
        evidence: Sequence[EvidenceParams],
        ref_id: str | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionEvidence:
        """Records a decision made outside Cortex with its evidence. Not retried."""
        call = _record(title, evidence, ref_id, confidence, metadata, source, request_options)
        return self._transport.request(call, DecisionEvidence)


class AsyncEvidence(AsyncAPIResource):
    async def list_decisions(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionPage:
        return await self._transport.request(_list(limit, offset, request_options), DecisionPage)

    def iter_decisions(self, *, page_size: int = 100) -> AsyncIterator[EvidenceNode]:
        return apaginate(
            lambda limit, offset: self.list_decisions(limit=limit, offset=offset),
            limit=page_size,
            offset=0,
        )

    async def decision(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> DecisionEvidence:
        return await self._transport.request(
            _decision(id, depth, request_options), DecisionEvidence
        )

    async def graph(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> EvidenceGraph:
        return await self._transport.request(_graph(id, depth, request_options), EvidenceGraph)

    async def node(
        self, id: str, *, depth: int | None = None, request_options: RequestOptions | None = None
    ) -> EvidenceNodeDetail:
        return await self._transport.request(_node(id, depth, request_options), EvidenceNodeDetail)

    async def path(
        self,
        source: str,
        target: str,
        *,
        directed: bool | None = None,
        depth: int | None = None,
        request_options: RequestOptions | None = None,
    ) -> EvidencePath:
        call = _path(source, target, directed, depth, request_options)
        return await self._transport.request(call, EvidencePath)

    async def record_decision(
        self,
        *,
        title: str,
        evidence: Sequence[EvidenceParams],
        ref_id: str | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
        request_options: RequestOptions | None = None,
    ) -> DecisionEvidence:
        call = _record(title, evidence, ref_id, confidence, metadata, source, request_options)
        return await self._transport.request(call, DecisionEvidence)
