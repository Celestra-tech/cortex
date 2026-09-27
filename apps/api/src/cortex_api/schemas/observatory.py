from datetime import datetime

from pydantic import BaseModel


class RequestStats(BaseModel):
    total: int
    failed: int
    success_rate: float | None
    avg_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    tokens: int
    cost_estimate: float
    fallbacks: int
    fallback_rate: float | None


class TimelineBucket(BaseModel):
    start: datetime
    requests: int
    failed: int
    avg_latency_ms: float | None
    p95_latency_ms: float | None


class ProviderShare(BaseModel):
    provider: str
    requests: int
    share: float
    avg_latency_ms: float
    tokens: int
    cost_estimate: float
    models: list[str]


class ConversationActivity(BaseModel):
    active: int
    """Live conversations updated inside the window."""
    total: int


class KnowledgeActivity(BaseModel):
    documents: int
    chunks: int
    tokens: int
    indexed_in_window: int
    queries_in_window: int
    avg_confidence: float | None
    zero_result_rate: float | None


class MemoryActivity(BaseModel):
    total: int


class OverviewResponse(BaseModel):
    window_hours: int
    generated_at: datetime
    bucket_seconds: int
    requests: RequestStats
    previous: RequestStats
    """Same-length window immediately before, for deltas."""
    timeline: list[TimelineBucket]
    providers: list[ProviderShare]
    conversations: ConversationActivity
    knowledge: KnowledgeActivity
    memories: MemoryActivity
