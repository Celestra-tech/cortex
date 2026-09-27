from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cortex_api.core.config import Settings
from cortex_api.models.document import Document
from cortex_api.models.organization import Organization
from cortex_api.services.knowledge.embeddings import (
    Embedder,
    EmbeddingProvider,
    EmbeddingTask,
    HashingEmbeddingProvider,
    ProviderEmbeddings,
)
from cortex_api.services.knowledge.extraction import DocumentFormat
from cortex_api.services.knowledge.ingestion import DocumentInput
from cortex_api.services.knowledge.service import KnowledgeService
from cortex_api.services.router.base import ProviderError, ProviderErrorKind

from ..router.fakes import SleepRecorder

DocumentFactory = Callable[..., Awaitable[Document]]

CORPUS = {
    "Refund Policy": (
        "# Refunds\n\nAnnual plans can be refunded within 30 days of purchase. "
        "Refunds are issued to the original payment method within 5 business days.\n\n"
        "# Chargebacks\n\nDisputed charges are reviewed by the billing team.",
        {"team": "billing", "tier": "public"},
    ),
    "Kubernetes Runbook": (
        "# Scheduling\n\nPods are scheduled onto nodes by the kube-scheduler. "
        "Use node affinity to pin workloads.\n\n"
        "# Incidents\n\nPage the platform on-call when the cluster is degraded.",
        {"team": "platform"},
    ),
    "Security Overview": (
        "Customer data is encrypted at rest with AES-256 and in transit with TLS 1.3. "
        "Access is reviewed quarterly.",
        {"team": "security", "tier": "public"},
    ),
}


class RecordingProvider(EmbeddingProvider):
    """Hashing embeddings with a configurable space, recorded inputs, and scripted failures."""

    def __init__(self, *, name: str = "recording", dimensions: int = 64) -> None:
        self.name = name
        self.model = "rec-1"
        self.dimensions = dimensions
        self._inner = HashingEmbeddingProvider(dimensions)
        self.inputs: list[tuple[EmbeddingTask, list[str]]] = []
        self.failures: list[ProviderError] = []

    @property
    def configured(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        self.inputs.append((task, list(texts)))
        if self.failures:
            raise self.failures.pop(0)
        return await self._inner.embed(texts, task)

    def fail(
        self, kind: ProviderErrorKind = ProviderErrorKind.AUTHENTICATION, times: int = 1
    ) -> None:
        self.failures.extend(
            ProviderError(self.name, kind, "embedding exploded") for _ in range(times)
        )


@pytest.fixture
def recording_provider() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
def knowledge_embedder(recording_provider: RecordingProvider) -> Embedder:
    return Embedder(recording_provider, max_retries=0, sleep=SleepRecorder())


@pytest.fixture
def knowledge(
    session: AsyncSession, app_settings: Settings, knowledge_embedder: Embedder
) -> KnowledgeService:
    """Service without a Redis cache; cache behaviour is tested separately."""
    return KnowledgeService.from_settings(session, None, app_settings, knowledge_embedder)


@pytest.fixture
def add_document(knowledge: KnowledgeService, organization: Organization) -> DocumentFactory:
    async def add(
        title: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
        fmt: DocumentFormat = DocumentFormat.MARKDOWN,
        organization_id: Any = None,
        service: KnowledgeService | None = None,
    ) -> Document:
        return await (service or knowledge).ingest_text(
            organization_id or organization.id,
            content,
            DocumentInput(title=title, source=f"kb://{title}", metadata=metadata or {}),
            fmt=fmt,
        )

    return add


@pytest.fixture
async def corpus(add_document: DocumentFactory) -> dict[str, Document]:
    return {
        title: await add_document(title, content, metadata=metadata)
        for title, (content, metadata) in CORPUS.items()
    }
