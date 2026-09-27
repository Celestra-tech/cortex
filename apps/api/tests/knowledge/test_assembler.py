import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.models.document import Document
from cortex_api.models.document_chunk import DocumentChunk
from cortex_api.models.knowledge_query import KnowledgeQuery, SearchMode
from cortex_api.models.organization import Organization
from cortex_api.services.knowledge.assembler import confidence, merge_adjacent
from cortex_api.services.knowledge.citations import Citation, cited_indices, snippet
from cortex_api.services.knowledge.hybrid_search import FusedScore, SearchHit
from cortex_api.services.knowledge.service import KnowledgeService

from .conftest import DocumentFactory

TEXT = "Alpha one. Beta two. Gamma three. Delta four. Epsilon five."


def make_hit(
    document: Document,
    index: int,
    start: int,
    end: int,
    score: float,
    *,
    fused: FusedScore | None = None,
    lexemes: frozenset[str] = frozenset(),
) -> SearchHit:
    chunk = DocumentChunk(
        id=uuid.uuid4(),
        document_id=document.id,
        chunk_index=index,
        content=TEXT[start:end],
        char_start=start,
        char_end=end,
        section=None,
        page_start=None,
        page_end=None,
    )
    return SearchHit(chunk, document, score, fused or FusedScore(score), 1.0, lexemes)


def make_document(title: str = "Doc") -> Document:
    return Document(id=uuid.uuid4(), title=title, source=None, created_at=datetime.now(UTC))


class TestMerging:
    def test_overlapping_neighbours_merge_without_duplication(self) -> None:
        doc = make_document()
        # Chunk 1 overlaps chunk 0 by "Beta two. "; chunk 2 follows chunk 1 exactly.
        hits = [
            make_hit(doc, 0, 0, 20, 0.5),
            make_hit(doc, 1, 11, 33, 0.9),
            make_hit(doc, 2, 34, 45, 0.2),
        ]
        [passage] = merge_adjacent(hits)
        assert passage.text == "Alpha one. Beta two. Gamma three.\n\nDelta four."
        assert passage.score == 0.9
        assert passage.lead.chunk.chunk_index == 1

    def test_gaps_and_documents_split_passages_ordered_by_score(self) -> None:
        first, second = make_document("First"), make_document("Second")
        hits = [
            make_hit(first, 0, 0, 10, 0.3),
            make_hit(first, 4, 47, 60, 0.8),
            make_hit(second, 0, 0, 10, 0.6),
        ]
        passages = merge_adjacent(hits)
        assert [(p.document_id, p.score) for p in passages] == [
            (first.id, 0.8),
            (second.id, 0.6),
            (first.id, 0.3),
        ]


class TestConfidence:
    def test_blends_similarity_coverage_and_agreement(self) -> None:
        doc = make_document()
        hits = [
            make_hit(
                doc,
                0,
                0,
                10,
                0.9,
                fused=FusedScore(0.9, 1, 0.7, 1, 0.3),
                lexemes=frozenset({"refund", "annual"}),
            ),
            make_hit(doc, 3, 34, 46, 0.4, fused=FusedScore(0.4, 2, 0.5, None, None)),
        ]
        result = confidence(
            merge_adjacent(hits),
            frozenset({"refund", "annual", "plan"}),
            (0.2, 0.8),
            SearchMode.HYBRID,
        )
        assert result.similarity == pytest.approx(0.5 / 0.6)
        assert result.coverage == pytest.approx(2 / 3)
        assert result.agreement == pytest.approx(0.5)
        expected = 0.5 * (0.5 / 0.6) + 0.35 * (2 / 3) + 0.15 * 0.5
        assert result.score == pytest.approx(expected, abs=1e-4)
        assert result.level == "high"

    @pytest.mark.parametrize(
        ("coverage_terms", "level"),
        [({"alpha"}, "high"), ({"alpha", "b"}, "medium"), ({"alpha", "b", "c", "d", "e"}, "low")],
    )
    def test_levels(self, coverage_terms: set[str], level: str) -> None:
        doc = make_document()
        hits = [make_hit(doc, 0, 0, 10, 1.0, lexemes=frozenset({"alpha"}))]
        result = confidence(
            merge_adjacent(hits), frozenset(coverage_terms), (0.2, 0.8), SearchMode.KEYWORD
        )
        assert result.level == level

    def test_missing_signals_are_renormalized(self) -> None:
        doc = make_document()
        hits = [make_hit(doc, 0, 0, 10, 1.0, lexemes=frozenset({"alpha"}))]
        result = confidence(
            merge_adjacent(hits), frozenset({"alpha"}), (0.2, 0.8), SearchMode.KEYWORD
        )
        assert (result.similarity, result.agreement) == (None, None)
        assert result.score == 1.0 and result.level == "high"

    def test_nothing_retrieved(self) -> None:
        result = confidence([], frozenset({"x"}), (0.2, 0.8), SearchMode.HYBRID)
        assert (result.score, result.level) == (0.0, "none")


class TestCitations:
    @pytest.mark.parametrize(
        ("output", "count", "expected"),
        [
            ("Refunds take 5 days [2]. Plans renew yearly [1][2].", 3, [2, 1]),
            ("See [1, 3] and [3].", 3, [1, 3]),
            ("Out of range [4] and [0] are ignored; [x] too.", 3, []),
            ("No citations at all.", 3, []),
        ],
    )
    def test_cited_indices(self, output: str, count: int, expected: list[int]) -> None:
        assert cited_indices(output, count) == expected

    def test_snippet_picks_the_best_matching_sentence(self) -> None:
        content = "Intro sentence here. Refunds are issued within five days. Closing remark."
        assert snippet(content, ["refund", "day"]) == "Refunds are issued within five days."
        long = "word " * 100
        trimmed = snippet(long, [], max_chars=30)
        assert len(trimmed) <= 30 and trimmed.endswith("…")

    def test_label(self) -> None:
        base = dict(
            index=1,
            document_id=uuid.uuid4(),
            chunk_ids=(uuid.uuid4(),),
            title="Handbook",
            source=None,
            score=1.0,
            snippet="",
        )
        assert Citation(**base, section="Leave", page_start=3, page_end=4).label == (  # type: ignore[arg-type]
            "Handbook > Leave (pp. 3-4)"
        )
        assert Citation(**base, section=None, page_start=2, page_end=2).label == "Handbook (p. 2)"  # type: ignore[arg-type]
        assert Citation(**base, section=None, page_start=None, page_end=None).label == "Handbook"  # type: ignore[arg-type]
        repeated = Citation(**base, section="handbook > Leave", page_start=None, page_end=None)  # type: ignore[arg-type]
        assert repeated.label == "Handbook > Leave"


@pytest.mark.database
class TestAssembly:
    async def test_context_is_numbered_budgeted_and_logged(
        self,
        session: AsyncSession,
        knowledge: KnowledgeService,
        organization: Organization,
        corpus: dict[str, Document],
    ) -> None:
        retrieval = await knowledge.retrieve(
            organization.id, "how long do refunds take for annual plans", top_k=5
        )
        context = retrieval.context
        assert context.citations, "expected at least one citation"
        assert [c.index for c in context.citations] == list(range(1, len(context.citations) + 1))
        first = context.citations[0]
        assert context.text.startswith(f"[1] {first.label}\n")
        assert first.title == "Refund Policy"
        assert set(first.chunk_ids) <= {hit.chunk.id for hit in context.hits}
        assert 0 < context.confidence.score <= 1
        assert context.token_count > 0

        entry = (
            await session.execute(
                select(KnowledgeQuery).where(KnowledgeQuery.id == retrieval.query_id)
            )
        ).scalar_one()
        assert entry.organization_id == organization.id
        assert entry.mode is SearchMode.HYBRID
        assert entry.top_k == 5
        assert entry.result_count == len(context.hits)
        assert entry.citation_count == len(context.citations)
        assert entry.context_tokens == context.token_count
        assert entry.confidence == context.confidence.score
        assert entry.embedding_space == "recording/rec-1@64"
        assert entry.completion_id is None and entry.cited is None
        assert entry.results[0]["citation"] == 1
        assert {r["chunk_id"] for r in entry.results} == {str(h.chunk.id) for h in context.hits}

    async def test_budget_skips_passages_that_do_not_fit(
        self,
        knowledge: KnowledgeService,
        organization: Organization,
        add_document: DocumentFactory,
    ) -> None:
        await add_document("Huge", "Refund " + "details " * 400)
        await add_document("Tiny", "Refund window is 30 days.")
        retrieval = await knowledge.retrieve(
            organization.id, "refund", top_k=5, max_context_tokens=100, mode=SearchMode.KEYWORD
        )
        context = retrieval.context
        assert context.truncated
        assert [c.title for c in context.citations] == ["Tiny"]
        assert context.token_count <= 100

    async def test_empty_corpus(
        self, knowledge: KnowledgeService, organization: Organization
    ) -> None:
        retrieval = await knowledge.retrieve(organization.id, "anything at all", top_k=3)
        assert retrieval.context.citations == []
        assert retrieval.context.text == ""
        assert retrieval.context.confidence.level == "none"
