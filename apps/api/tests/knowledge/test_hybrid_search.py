import uuid
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.models.document import Document
from cortex_api.models.knowledge_query import SearchMode
from cortex_api.models.organization import Organization
from cortex_api.repositories.knowledge_repository import ChunkRepository, DocumentFilters
from cortex_api.services.knowledge.embeddings import Embedder, QueryEmbeddingCache
from cortex_api.services.knowledge.hybrid_search import (
    HybridSearcher,
    SearchConfig,
    VectorSearchUnavailableError,
    recency_factor,
    reciprocal_rank_fusion,
)
from cortex_api.services.knowledge.service import KnowledgeService

from ..conftest import OrganizationFactory
from .conftest import DocumentFactory, RecordingProvider

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class TestFusion:
    def test_agreement_beats_a_single_retriever(self) -> None:
        fused = reciprocal_rank_fusion([(A, 0.9), (B, 0.8)], [(B, 0.5), (C, 0.4)], k=60)
        assert fused[B].score > fused[A].score > fused[C].score
        assert fused[B].both and not fused[A].both
        assert (fused[B].vector_rank, fused[B].keyword_rank) == (2, 1)
        assert fused[A].vector_similarity == 0.9 and fused[A].keyword_score is None

    def test_first_in_every_active_list_scores_one(self) -> None:
        fused = reciprocal_rank_fusion([(A, 0.9)], [(A, 0.5)], k=60)
        assert fused[A].score == pytest.approx(1.0)
        # With one list empty, normalization only counts the active retriever.
        solo = reciprocal_rank_fusion([], [(A, 0.5), (B, 0.3)], k=60)
        assert solo[A].score == pytest.approx(1.0)
        assert solo[B].score == pytest.approx(61 / 62)

    def test_weights(self) -> None:
        fused = reciprocal_rank_fusion(
            [(A, 0.9)], [(B, 0.5)], k=60, vector_weight=3.0, keyword_weight=1.0
        )
        assert fused[A].score == pytest.approx(0.75)
        assert fused[B].score == pytest.approx(0.25)
        assert reciprocal_rank_fusion([], []) == {}
        assert reciprocal_rank_fusion([(A, 1.0)], [], vector_weight=0.0) == {}

    def test_recency_halves_every_half_life(self) -> None:
        now = datetime(2026, 9, 27, tzinfo=UTC)
        assert recency_factor(now, now, 30) == 1.0
        assert recency_factor(now - timedelta(days=30), now, 30) == pytest.approx(0.5)
        assert recency_factor(now + timedelta(days=1), now, 30) == 1.0  # clock skew

    def test_candidate_pool_is_bounded(self) -> None:
        config = SearchConfig(candidate_multiplier=4, min_candidates=40, max_candidates=100)
        assert (config.pool(1), config.pool(20), config.pool(50)) == (40, 80, 100)


pytestmark_db = pytest.mark.database


def titles(result_hits: list) -> list[str]:  # type: ignore[type-arg]
    return [hit.document.title for hit in result_hits]


@pytestmark_db
class TestRetrieval:
    async def test_keyword_mode(
        self, knowledge: KnowledgeService, organization: Organization, corpus: dict[str, Document]
    ) -> None:
        result = await knowledge.assembler.searcher.search(
            organization.id, "refunded", top_k=5, mode=SearchMode.KEYWORD
        )
        assert titles(result.hits) == ["Refund Policy"]
        hit = result.hits[0]
        assert hit.chunk.section == "Refunds"
        assert hit.fused.keyword_rank == 1 and hit.fused.vector_rank is None
        assert result.stats.vector_candidates == 0 and result.stats.embedding_space is None
        assert "refund" in result.stats.query_lexemes

    async def test_vector_mode(
        self,
        knowledge: KnowledgeService,
        organization: Organization,
        corpus: dict[str, Document],
        recording_provider: RecordingProvider,
    ) -> None:
        result = await knowledge.assembler.searcher.search(
            organization.id, "kube-scheduler node affinity pods", top_k=3, mode=SearchMode.VECTOR
        )
        assert result.hits[0].document.title == "Kubernetes Runbook"
        assert result.hits[0].fused.vector_similarity is not None
        assert result.hits[0].fused.keyword_rank is None
        assert result.stats.keyword_candidates == 0
        assert result.stats.embedding_space == "recording/rec-1@64"
        assert recording_provider.inputs[-1][0].value == "query"

    async def test_hybrid_mode_fuses_both(
        self, knowledge: KnowledgeService, organization: Organization, corpus: dict[str, Document]
    ) -> None:
        result = await knowledge.assembler.searcher.search(
            organization.id, "how long do refunds take for annual plans", top_k=4
        )
        top = result.hits[0]
        assert top.document.title == "Refund Policy" and top.chunk.section == "Refunds"
        assert top.fused.both
        assert result.stats.vector_candidates > 0 and result.stats.keyword_candidates > 0
        scores = [hit.score for hit in result.hits]
        assert scores == sorted(scores, reverse=True)
        assert len(result.hits) <= 4

    async def test_metadata_and_document_filters(
        self, knowledge: KnowledgeService, organization: Organization, corpus: dict[str, Document]
    ) -> None:
        search = knowledge.assembler.searcher.search
        query = "team data cluster refunds"
        platform = await search(
            organization.id, query, top_k=10, filters=DocumentFilters(metadata={"team": "platform"})
        )
        assert set(titles(platform.hits)) == {"Kubernetes Runbook"}

        public = await search(
            organization.id, query, top_k=10, filters=DocumentFilters(metadata={"tier": "public"})
        )
        assert set(titles(public.hits)) == {"Refund Policy", "Security Overview"}

        by_id = await search(
            organization.id,
            query,
            top_k=10,
            filters=DocumentFilters(document_ids=(corpus["Security Overview"].id,)),
        )
        assert set(titles(by_id.hits)) == {"Security Overview"}

        by_source = await search(
            organization.id,
            query,
            top_k=10,
            filters=DocumentFilters(sources=("kb://Refund Policy",)),
        )
        assert set(titles(by_source.hits)) == {"Refund Policy"}

        future = await search(
            organization.id,
            query,
            top_k=10,
            filters=DocumentFilters(created_after=datetime.now(UTC) + timedelta(days=1)),
        )
        assert future.hits == []

    async def test_organizations_are_isolated(
        self,
        knowledge: KnowledgeService,
        organization: Organization,
        organization_factory: OrganizationFactory,
        add_document: DocumentFactory,
        corpus: dict[str, Document],
    ) -> None:
        other = await organization_factory()
        await add_document(
            "Secret Refund Memo", "Refunds for the other org.", organization_id=other.id
        )
        mine = await knowledge.assembler.searcher.search(organization.id, "refunds", top_k=10)
        assert "Secret Refund Memo" not in titles(mine.hits)
        theirs = await knowledge.assembler.searcher.search(other.id, "refunds", top_k=10)
        assert titles(theirs.hits) == ["Secret Refund Memo"]

    async def test_vectors_from_another_space_are_not_compared(
        self,
        session: AsyncSession,
        app_settings: Settings,
        knowledge: KnowledgeService,
        organization: Organization,
        add_document: DocumentFactory,
    ) -> None:
        legacy = KnowledgeService.from_settings(
            session, None, app_settings, Embedder(RecordingProvider(name="legacy", dimensions=32))
        )
        await add_document("Legacy Refunds", "Refunds from the legacy space.", service=legacy)
        vector = await knowledge.assembler.searcher.search(
            organization.id, "legacy refunds", top_k=5, mode=SearchMode.VECTOR
        )
        assert vector.hits == []
        keyword = await knowledge.assembler.searcher.search(
            organization.id, "legacy refunds", top_k=5, mode=SearchMode.KEYWORD
        )
        assert titles(keyword.hits) == ["Legacy Refunds"]

    async def test_embedding_outage_degrades_hybrid_and_fails_vector(
        self,
        knowledge: KnowledgeService,
        organization: Organization,
        corpus: dict[str, Document],
        recording_provider: RecordingProvider,
    ) -> None:
        recording_provider.fail()
        degraded = await knowledge.assembler.searcher.search(organization.id, "refunds", top_k=3)
        assert titles(degraded.hits)[0] == "Refund Policy"
        assert degraded.stats.vector_candidates == 0
        assert degraded.stats.warnings and "vector search skipped" in degraded.stats.warnings[0]

        recording_provider.fail()
        with pytest.raises(VectorSearchUnavailableError):
            await knowledge.assembler.searcher.search(
                organization.id, "refunds", top_k=3, mode=SearchMode.VECTOR
            )

    async def test_recency_boost_prefers_newer_documents(
        self,
        session: AsyncSession,
        knowledge: KnowledgeService,
        organization: Organization,
        add_document: DocumentFactory,
    ) -> None:
        old = await add_document("Old Pricing", "Pricing for the starter plan is 10 USD.")
        new = await add_document("New Pricing", "Pricing for the starter plan is 12 USD!")
        await session.execute(
            update(Document)
            .where(Document.id == old.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=720))
        )
        search = knowledge.assembler.searcher.search
        boosted = await search(organization.id, "starter plan pricing", top_k=2, recency_weight=1.0)
        assert titles(boosted.hits)[0] == new.title
        assert boosted.hits[0].recency > 0.99 > boosted.hits[1].recency
        for hit in boosted.hits:
            assert hit.score == pytest.approx(hit.fused.score * hit.recency)
        # Weight 0 disables the boost entirely.
        neutral = await search(organization.id, "starter plan pricing", top_k=2, recency_weight=0.0)
        for hit in neutral.hits:
            assert hit.score == pytest.approx(hit.fused.score)
        # Partial weight bounds the loss: an old document keeps at least (1 - w) of its score.
        partial = await search(
            organization.id, "starter plan pricing", top_k=2, recency_weight=0.25
        )
        for hit in partial.hits:
            assert hit.fused.score * 0.75 <= hit.score <= hit.fused.score

    async def test_min_score_and_top_k(
        self, knowledge: KnowledgeService, organization: Organization, corpus: dict[str, Document]
    ) -> None:
        search = knowledge.assembler.searcher.search
        one = await search(organization.id, "data refunds pods", top_k=1)
        assert len(one.hits) == 1
        none = await search(organization.id, "data refunds pods", top_k=5, min_score=1.01)
        assert none.hits == []


@pytestmark_db
@pytest.mark.redis
async def test_query_embeddings_are_cached(
    session: AsyncSession,
    redis: Redis,
    redis_key_prefix: str,
    organization: Organization,
    corpus: dict[str, Document],
    knowledge_embedder: Embedder,
    recording_provider: RecordingProvider,
) -> None:
    searcher = HybridSearcher(
        ChunkRepository(session),
        knowledge_embedder,
        cache=QueryEmbeddingCache(redis, prefix=redis_key_prefix),
    )
    calls = len(recording_provider.inputs)
    first = await searcher.search(organization.id, "encrypted at rest", top_k=3)
    second = await searcher.search(organization.id, "encrypted   at rest", top_k=3)
    assert (first.stats.query_embedding_cached, second.stats.query_embedding_cached) == (
        False,
        True,
    )
    assert len(recording_provider.inputs) == calls + 1
    assert [h.chunk.id for h in first.hits] == [h.chunk.id for h in second.hits]
