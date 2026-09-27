"""The Evidence Graph: every decision linked to the exact records that produced it."""

from cortex_api.services.evidence.graph import (
    EvidenceGraph,
    EvidenceGraphLoader,
    EvidenceNotFoundError,
)
from cortex_api.services.evidence.linker import (
    CompletionTrail,
    DecisionExistsError,
    EvidenceLinker,
    EvidencePlan,
    RecordedDecision,
)
from cortex_api.services.evidence.provenance import Provenance, ProvenanceError, Source
from cortex_api.services.evidence.traversal import (
    EvidenceTraversal,
    ancestors,
    contradicting_evidence,
    descendants,
    shortest_path,
    supporting_evidence,
)

__all__ = [
    "CompletionTrail",
    "DecisionExistsError",
    "EvidenceGraph",
    "EvidenceGraphLoader",
    "EvidenceLinker",
    "EvidenceNotFoundError",
    "EvidencePlan",
    "EvidenceTraversal",
    "Provenance",
    "ProvenanceError",
    "RecordedDecision",
    "Source",
    "ancestors",
    "contradicting_evidence",
    "descendants",
    "shortest_path",
    "supporting_evidence",
]
