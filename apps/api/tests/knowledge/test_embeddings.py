import json
import math
from collections.abc import Callable, Sequence

import httpx2
import pytest
from redis.asyncio import Redis

from cortex_api.core.config import Settings
from cortex_api.models.document_chunk import EMBEDDING_DIMENSIONS
from cortex_api.services.knowledge.embeddings import (
    Embedder,
    EmbeddingProvider,
    EmbeddingTask,
    GeminiEmbeddingProvider,
    HashingEmbeddingProvider,
    OpenAIEmbeddingProvider,
    ProviderEmbeddings,
    QueryEmbeddingCache,
    build_embedding_provider,
    normalize_vector,
    to_index_vector,
)
from cortex_api.services.router.base import HTTPProviderConfig, ProviderError, ProviderErrorKind

from ..router.fakes import SleepRecorder

Handler = Callable[[httpx2.Request], httpx2.Response]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def client(handler: Handler) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


class ScriptedProvider(EmbeddingProvider):
    name = "scripted"
    model = "m"

    def __init__(self, dimensions: int = 4, *, max_batch_size: int = 256) -> None:
        self.dimensions = dimensions
        self.max_batch_size = max_batch_size
        self.batches: list[list[str]] = []
        self.failures: list[ProviderError] = []

    @property
    def configured(self) -> bool:
        return True

    async def embed(self, texts: Sequence[str], task: EmbeddingTask) -> ProviderEmbeddings:
        self.batches.append(list(texts))
        if self.failures:
            raise self.failures.pop(0)
        return ProviderEmbeddings(
            [[float(len(t)), 1.0, 0.0, 0.0] for t in texts], tokens=len(texts)
        )


class TestHashing:
    async def test_deterministic_and_lexical(self) -> None:
        provider = HashingEmbeddingProvider()
        texts = [
            "refund policy for annual plans",
            "annual plans refund policy",
            "kubernetes pod scheduling",
        ]
        first = (await provider.embed(texts, EmbeddingTask.DOCUMENT)).vectors
        again = (await provider.embed(texts, EmbeddingTask.QUERY)).vectors
        assert first == again
        assert len(first[0]) == EMBEDDING_DIMENSIONS
        assert cosine(first[0], first[1]) > 0.5
        assert cosine(first[0], first[1]) > cosine(first[0], first[2]) + 0.4

    async def test_text_without_words_is_a_zero_vector(self) -> None:
        [vector] = (await HashingEmbeddingProvider(16).embed(["!!!"], EmbeddingTask.QUERY)).vectors
        assert normalize_vector(vector) is None


class TestVectorMath:
    def test_normalize(self) -> None:
        unit = normalize_vector([3.0, 4.0])
        assert unit == pytest.approx([0.6, 0.8])
        assert normalize_vector([0.0, 0.0]) is None
        assert normalize_vector([math.inf, 1.0]) is None

    def test_padding_preserves_cosine(self) -> None:
        a, b = [0.3, -0.2, 0.9], [0.1, 0.5, 0.4]
        padded_a, padded_b = to_index_vector(a), to_index_vector(b)
        assert len(padded_a) == EMBEDDING_DIMENSIONS
        assert cosine(padded_a, padded_b) == pytest.approx(cosine(a, b))
        with pytest.raises(ValueError):
            to_index_vector([0.0] * (EMBEDDING_DIMENSIONS + 1))


class TestOpenAI:
    async def test_request_and_response_mapping(self) -> None:
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(
                200,
                json={
                    # Out of order on purpose: the adapter must sort by index.
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                        {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    ],
                    "usage": {"prompt_tokens": 7},
                },
            )

        async with client(handler) as http:
            provider = OpenAIEmbeddingProvider(
                http, HTTPProviderConfig("sk-test", "https://api.openai.test/v1"), dimensions=3
            )
            result = await provider.embed(["a", "b"], EmbeddingTask.DOCUMENT)

        assert result.vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        assert result.tokens == 7
        [request] = seen
        assert str(request.url) == "https://api.openai.test/v1/embeddings"
        assert request.headers["authorization"] == "Bearer sk-test"
        assert json.loads(request.content) == {
            "model": "text-embedding-3-small",
            "input": ["a", "b"],
            "encoding_format": "float",
            "dimensions": 3,
        }
        assert provider.space == "openai/text-embedding-3-small@3"

    async def test_self_hosted_compatible_server(self) -> None:
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.5]}]})

        async with client(handler) as http:
            provider = OpenAIEmbeddingProvider(
                http,
                HTTPProviderConfig(None, "http://ollama.local/v1"),
                model="nomic-embed-text",
                dimensions=2,
                require_key=False,
            )
            assert provider.configured
            await provider.embed(["x"], EmbeddingTask.QUERY)
        assert "authorization" not in seen[0].headers
        assert "dimensions" not in json.loads(seen[0].content)

    async def test_missing_key_and_bad_payloads(self) -> None:
        async with client(lambda r: httpx2.Response(200, json={"data": []})) as http:
            unkeyed = OpenAIEmbeddingProvider(http, HTTPProviderConfig(None, "https://x.test"))
            assert not unkeyed.configured
            with pytest.raises(ProviderError) as missing:
                await unkeyed.embed(["x"], EmbeddingTask.QUERY)
            assert missing.value.kind is ProviderErrorKind.UNAVAILABLE

            keyed = OpenAIEmbeddingProvider(http, HTTPProviderConfig("k", "https://x.test"))
            with pytest.raises(ProviderError) as mismatch:
                await keyed.embed(["x"], EmbeddingTask.QUERY)
            assert mismatch.value.kind is ProviderErrorKind.INVALID_RESPONSE

        wrong_width = {"data": [{"index": 0, "embedding": [1.0, 2.0]}]}
        async with client(lambda r: httpx2.Response(200, json=wrong_width)) as http:
            provider = OpenAIEmbeddingProvider(
                http, HTTPProviderConfig("k", "https://x.test"), dimensions=3
            )
            with pytest.raises(ProviderError, match="3-dimension"):
                await provider.embed(["x"], EmbeddingTask.QUERY)

    async def test_http_errors_are_classified(self) -> None:
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(
                429, json={"error": {"message": "slow down"}}, headers={"retry-after": "2"}
            )

        async with client(handler) as http:
            provider = OpenAIEmbeddingProvider(http, HTTPProviderConfig("k", "https://x.test"))
            with pytest.raises(ProviderError) as caught:
                await provider.embed(["x"], EmbeddingTask.QUERY)
        assert caught.value.kind is ProviderErrorKind.RATE_LIMITED
        assert caught.value.retry_after_seconds == 2.0
        assert caught.value.provider == "openai"


class TestGemini:
    async def test_request_and_response_mapping(self) -> None:
        seen: list[httpx2.Request] = []

        def handler(request: httpx2.Request) -> httpx2.Response:
            seen.append(request)
            return httpx2.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}]})

        async with client(handler) as http:
            provider = GeminiEmbeddingProvider(
                http, HTTPProviderConfig("g-key", "https://gemini.test/v1beta"), dimensions=2
            )
            result = await provider.embed(["hello"], EmbeddingTask.QUERY)

        assert result.vectors == [[0.1, 0.2]]
        assert result.tokens is None
        [request] = seen
        assert str(request.url) == (
            "https://gemini.test/v1beta/models/gemini-embedding-001:batchEmbedContents"
        )
        assert request.headers["x-goog-api-key"] == "g-key"
        assert "g-key" not in str(request.url)
        assert json.loads(request.content) == {
            "requests": [
                {
                    "model": "models/gemini-embedding-001",
                    "content": {"parts": [{"text": "hello"}]},
                    "taskType": "RETRIEVAL_QUERY",
                    "outputDimensionality": 2,
                }
            ]
        }


class TestEmbedder:
    async def test_batches_normalizes_and_counts_tokens(self) -> None:
        provider = ScriptedProvider(max_batch_size=2)
        embedder = Embedder(provider, batch_size=10, max_concurrency=2)
        assert embedder.batch_size == 2  # capped by the provider
        result = await embedder.embed(["aa", "b", "cccc", "d", "e"], EmbeddingTask.DOCUMENT)
        assert provider.batches == [["aa", "b"], ["cccc", "d"], ["e"]]
        assert result.batches == 3
        assert result.tokens == 5
        for vector in result.vectors:
            assert vector is not None
            assert math.fsum(v * v for v in vector) == pytest.approx(1.0)

    async def test_retries_transient_errors_honoring_retry_after(self) -> None:
        provider = ScriptedProvider()
        provider.failures = [
            ProviderError("scripted", ProviderErrorKind.RATE_LIMITED, "429", retry_after_seconds=3),
            ProviderError("scripted", ProviderErrorKind.SERVER, "500"),
        ]
        sleeps = SleepRecorder()
        embedder = Embedder(provider, max_retries=2, backoff_base_seconds=1.0, sleep=sleeps)
        result = await embedder.embed(["x"], EmbeddingTask.DOCUMENT)
        assert len(result.vectors) == 1
        assert len(provider.batches) == 3
        assert sleeps.delays[0] == 3
        assert 2.0 <= sleeps.delays[1] <= 4.0  # jitter within [ceiling / 2, ceiling]

    async def test_gives_up_on_permanent_errors_and_exhausted_retries(self) -> None:
        provider = ScriptedProvider()
        provider.failures = [ProviderError("scripted", ProviderErrorKind.AUTHENTICATION, "no")]
        with pytest.raises(ProviderError):
            await Embedder(provider, sleep=SleepRecorder()).embed(["x"], EmbeddingTask.QUERY)
        assert len(provider.batches) == 1

        provider.failures = [ProviderError("scripted", ProviderErrorKind.TIMEOUT, "t")] * 3
        with pytest.raises(ProviderError):
            await Embedder(provider, max_retries=1, sleep=SleepRecorder()).embed(
                ["x"], EmbeddingTask.QUERY
            )


def test_build_embedding_provider_from_settings() -> None:
    http = httpx2.AsyncClient()
    local = build_embedding_provider(Settings(_env_file=None), http)
    assert local.space == f"local/hashing-v1@{EMBEDDING_DIMENSIONS}"

    openai = build_embedding_provider(
        Settings(knowledge_embedding_provider="openai", openai_api_key="sk", _env_file=None), http
    )
    assert isinstance(openai, OpenAIEmbeddingProvider)
    assert openai.configured and openai.require_key

    hosted = build_embedding_provider(
        Settings(
            knowledge_embedding_provider="openai",
            knowledge_embedding_base_url="http://vllm.local/v1",
            knowledge_embedding_model="bge-large",
            knowledge_embedding_dimensions=1024,
            _env_file=None,
        ),
        http,
    )
    assert hosted.configured and hosted.space == "openai/bge-large@1024"

    gemini = build_embedding_provider(
        Settings(knowledge_embedding_provider="gemini", gemini_api_key="g", _env_file=None), http
    )
    assert gemini.space == f"gemini/gemini-embedding-001@{EMBEDDING_DIMENSIONS}"


@pytest.mark.redis
async def test_query_cache_round_trip(redis: Redis, redis_key_prefix: str) -> None:
    cache = QueryEmbeddingCache(redis, prefix=redis_key_prefix, ttl_seconds=60)
    assert await cache.get("s", "q") is None
    await cache.set("s", "q", [0.25, -0.5, 1.0])
    assert await cache.get("s", "q") == [0.25, -0.5, 1.0]
    assert await cache.get("other-space", "q") is None
    assert 0 < await redis.ttl(cache.key("s", "q")) <= 60

    disabled = QueryEmbeddingCache(redis, prefix=redis_key_prefix, ttl_seconds=0)
    await disabled.set("s", "q2", [1.0])
    assert await disabled.get("s", "q") is None
    assert await redis.exists(cache.key("s", "q2")) == 0
