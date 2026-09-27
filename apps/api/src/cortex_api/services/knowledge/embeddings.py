"""Embedding providers, batching, and the query-embedding cache.

Every vector leaving this module is L2-normalized, so cosine distance in
pgvector equals 1 - dot product regardless of provider. Vectors narrower than
the indexed column are zero-padded by `to_index_vector`, which leaves cosine
similarity unchanged.
"""

import asyncio
import base64
import hashlib
import itertools
import logging
import math
import random
import re
import struct
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

import httpx2
from pydantic import SecretStr
from redis.asyncio import Redis
from redis.exceptions import RedisError

from cortex_api.core.config import Settings
from cortex_api.models.document_chunk import EMBEDDING_DIMENSIONS
from cortex_api.services.memory.encoder import HeuristicTokenEstimator, TokenEstimator
from cortex_api.services.router.base import (
    HTTPProviderConfig,
    JSONClient,
    ProviderError,
    ProviderErrorKind,
)

logger = logging.getLogger(__name__)

Vector = list[float]


class EmbeddingTask(StrEnum):
    DOCUMENT = "document"
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class ProviderEmbeddings:
    vectors: list[Vector]
    tokens: int | None = None


class EmbeddingProvider(ABC):
    """One embedding model. `dimensions` is the native output width."""

    name: str
    model: str
    dimensions: int
    max_batch_size: int = 256
    similarity_range: tuple[float, float] = (0.2, 0.8)
    """Cosine similarities mapped to 0 and 1 when computing retrieval confidence."""

    @property
    def space(self) -> str:
        """Vectors are only comparable within one space."""
        return f"{self.name}/{self.model}@{self.dimensions}"

    @property
    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings: ...


class HashingEmbeddingProvider(EmbeddingProvider):
    """Deterministic feature-hashing embedder: unigrams plus bigrams, signed, log-scaled.

    Needs no network or model weights, so development and tests work offline.
    Similarity is lexical (shared words and phrases), not semantic. Select a
    model-backed provider in production.
    """

    name = "local"
    model = "hashing-v1"
    max_batch_size = 4096
    similarity_range = (0.05, 0.6)

    _TOKEN = re.compile(r"\w+", re.UNICODE)

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions

    @property
    def configured(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        return ProviderEmbeddings([self._embed(text) for text in texts])

    def _embed(self, text: str) -> Vector:
        words = [w.lower() for w in self._TOKEN.findall(text)]
        counts: dict[str, float] = {}
        for word in words:
            counts[word] = counts.get(word, 0.0) + 1.0
        for left, right in itertools.pairwise(words):
            bigram = f"{left} {right}"
            counts[bigram] = counts.get(bigram, 0.0) + 0.5
        vector = [0.0] * self.dimensions
        for feature, count in counts.items():
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * (1.0 + math.log(count))
        return vector


class OpenAIEmbeddingProvider(JSONClient, EmbeddingProvider):
    """OpenAI `/embeddings`, and any server that speaks the same protocol."""

    name = "openai"
    max_batch_size = 2048
    similarity_range = (0.2, 0.65)

    def __init__(
        self,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = EMBEDDING_DIMENSIONS,
        send_dimensions: bool | None = None,
        require_key: bool = True,
    ) -> None:
        super().__init__(self.name, client, config)
        self.model = model
        self.dimensions = dimensions
        # Only the text-embedding-3 family accepts a requested width.
        self.send_dimensions = (
            model.startswith("text-embedding-3") if send_dimensions is None else send_dimensions
        )
        self.require_key = require_key

    @property
    def configured(self) -> bool:
        return bool(self.config.api_key) or not self.require_key

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": list(texts),
            "encoding_format": "float",
        }
        if self.send_dimensions:
            payload["dimensions"] = self.dimensions
        # `api_key` raises UNAVAILABLE when a required key is missing.
        needs_auth = self.require_key or bool(self.config.api_key)
        headers = {"Authorization": f"Bearer {self.api_key}"} if needs_auth else {}

        body = await self.post_json("/embeddings", payload, headers)
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(texts):
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "embedding count mismatch")
        ordered = sorted(
            data, key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0
        )
        vectors = [
            _vector(item.get("embedding") if isinstance(item, dict) else None, self)
            for item in ordered
        ]
        usage = body.get("usage")
        tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        return ProviderEmbeddings(vectors, tokens if isinstance(tokens, int) else None)


class GeminiEmbeddingProvider(JSONClient, EmbeddingProvider):
    """Gemini `batchEmbedContents` with retrieval task types."""

    name = "gemini"
    max_batch_size = 100
    similarity_range = (0.35, 0.8)

    _TASK_TYPES: ClassVar[Mapping[EmbeddingTask, str]] = {
        EmbeddingTask.DOCUMENT: "RETRIEVAL_DOCUMENT",
        EmbeddingTask.QUERY: "RETRIEVAL_QUERY",
    }

    def __init__(
        self,
        client: httpx2.AsyncClient,
        config: HTTPProviderConfig,
        *,
        model: str = "gemini-embedding-001",
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        super().__init__(self.name, client, config)
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        resource = f"models/{self.model}"
        payload = {
            "requests": [
                {
                    "model": resource,
                    "content": {"parts": [{"text": text}]},
                    "taskType": self._TASK_TYPES[task],
                    "outputDimensionality": self.dimensions,
                }
                for text in texts
            ]
        }
        body = await self.post_json(
            f"/{resource}:batchEmbedContents", payload, {"x-goog-api-key": self.api_key}
        )
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise self.error(ProviderErrorKind.INVALID_RESPONSE, "embedding count mismatch")
        vectors = [
            _vector(item.get("values") if isinstance(item, dict) else None, self)
            for item in embeddings
        ]
        return ProviderEmbeddings(vectors)


def _vector(raw: object, provider: EmbeddingProvider) -> Vector:
    if not isinstance(raw, list) or len(raw) != provider.dimensions:
        raise ProviderError(
            provider.name,
            ProviderErrorKind.INVALID_RESPONSE,
            f"expected a {provider.dimensions}-dimension vector",
        )
    try:
        return [float(value) for value in raw]
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            provider.name, ProviderErrorKind.INVALID_RESPONSE, "vector has non-numeric values"
        ) from exc


def normalize_vector(vector: Vector) -> Vector | None:
    """Unit length, or None for a zero vector (it has no direction to compare)."""
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm == 0.0 or not math.isfinite(norm):
        return None
    return [value / norm for value in vector]


def to_index_vector(vector: Vector) -> Vector:
    if len(vector) > EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"vector has {len(vector)} dimensions; the index holds {EMBEDDING_DIMENSIONS}"
        )
    return vector + [0.0] * (EMBEDDING_DIMENSIONS - len(vector))


# --- Batching and retries ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: list[Vector | None]
    """Unit vectors at native width; None where the text had nothing to embed."""
    tokens: int
    latency_ms: int
    batches: int


class Embedder:
    """Provider-sized batches, run a few at a time, with retries on transient errors."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        batch_size: int = 64,
        max_concurrency: int = 4,
        max_retries: int = 2,
        backoff_base_seconds: float = 0.5,
        backoff_max_seconds: float = 8.0,
        estimator: TokenEstimator | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.provider = provider
        self.batch_size = max(1, min(batch_size, provider.max_batch_size))
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self.estimator = estimator or HeuristicTokenEstimator()
        self.sleep = sleep

    @property
    def space(self) -> str:
        return self.provider.space

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> EmbeddingBatch:
        started = time.perf_counter()
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def run(batch: Sequence[str]) -> ProviderEmbeddings:
            async with semaphore:
                return await self._with_retries(batch, task)

        results = await asyncio.gather(*(run(batch) for batch in batches))
        vectors: list[Vector | None] = []
        tokens = 0
        for batch, result in zip(batches, results, strict=True):
            vectors.extend(normalize_vector(vector) for vector in result.vectors)
            tokens += (
                result.tokens
                if result.tokens is not None
                else sum(self.estimator.count(text) for text in batch)
            )
        return EmbeddingBatch(vectors, tokens, _elapsed_ms(started), len(batches))

    async def _with_retries(self, batch: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        attempt = 0
        while True:
            try:
                return await self.provider.embed(batch, task)
            except ProviderError as error:
                if not error.retryable or attempt >= self.max_retries:
                    raise
                attempt += 1
                ceiling = min(self.backoff_max_seconds, self.backoff_base_seconds * 2**attempt)
                delay = (
                    min(error.retry_after_seconds, self.backoff_max_seconds)
                    if error.retry_after_seconds is not None
                    else random.uniform(ceiling / 2, ceiling)  # noqa: S311 - jitter, not crypto
                )
                logger.warning(
                    "Embedding batch failed on %s (attempt %d): %s",
                    self.space,
                    attempt,
                    error.describe(),
                )
                await self.sleep(delay)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


# --- Query embedding cache -----------------------------------------------------------------------


class QueryEmbeddingCache:
    """Redis cache of query vectors keyed by space and query hash. Best effort."""

    def __init__(self, redis: Redis, *, prefix: str = "", ttl_seconds: int = 86_400) -> None:
        self.redis = redis
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds

    def key(self, space: str, query: str) -> str:
        digest = hashlib.sha256(query.encode()).hexdigest()
        return f"{self.prefix}knowledge:qemb:{space}:{digest}"

    async def get(self, space: str, query: str) -> Vector | None:
        if self.ttl_seconds == 0:
            return None
        try:
            raw = await self.redis.get(self.key(space, query))
        except RedisError as exc:
            logger.warning("Query embedding cache read failed: %s", exc)
            return None
        if not raw:
            return None
        try:
            packed = base64.b64decode(raw)
            return list(struct.unpack(f"<{len(packed) // 4}f", packed))
        except (ValueError, struct.error):
            return None

    async def set(self, space: str, query: str, vector: Vector) -> None:
        if self.ttl_seconds == 0:
            return
        packed = base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()
        try:
            await self.redis.set(self.key(space, query), packed, ex=self.ttl_seconds)
        except RedisError as exc:
            logger.warning("Query embedding cache write failed: %s", exc)


# --- Construction --------------------------------------------------------------------------------


def build_embedding_provider(settings: Settings, client: httpx2.AsyncClient) -> EmbeddingProvider:
    dimensions = settings.knowledge_embedding_dimensions
    model = settings.knowledge_embedding_model
    timeout = settings.router_timeout_seconds
    match settings.knowledge_embedding_provider:
        case "openai":
            base_url = settings.knowledge_embedding_base_url or settings.openai_base_url
            return OpenAIEmbeddingProvider(
                client,
                HTTPProviderConfig(_secret(settings.openai_api_key), base_url, timeout),
                model=model or "text-embedding-3-small",
                dimensions=dimensions,
                # Self-hosted OpenAI-compatible servers usually run without a key.
                require_key=settings.knowledge_embedding_base_url is None,
            )
        case "gemini":
            return GeminiEmbeddingProvider(
                client,
                HTTPProviderConfig(
                    _secret(settings.gemini_api_key),
                    settings.knowledge_embedding_base_url or settings.gemini_base_url,
                    timeout,
                ),
                model=model or "gemini-embedding-001",
                dimensions=dimensions,
            )
        case "local":
            return HashingEmbeddingProvider(dimensions)


def build_embedder(settings: Settings, client: httpx2.AsyncClient) -> Embedder:
    return Embedder(
        build_embedding_provider(settings, client),
        batch_size=settings.knowledge_embedding_batch_size,
        max_concurrency=settings.knowledge_embedding_max_concurrency,
        max_retries=settings.knowledge_embedding_max_retries,
    )


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None
