import uuid
from dataclasses import dataclass

from sqlalchemy import Float, Select, String, cast, func, literal, select
from sqlalchemy.dialects.postgresql import TSQUERY
from sqlalchemy.sql.elements import ColumnElement

from cortex_api.models.memory import SEARCH_CONFIG, Memory, MemoryType

SECONDS_PER_DAY = 86_400.0
MAX_QUERY_CHARS = 1_000


@dataclass(frozen=True, slots=True)
class MemoryQuery:
    text: str | None = None
    types: frozenset[MemoryType] | None = None
    min_importance: float = 0.0
    limit: int = 10

    @property
    def normalized_text(self) -> str | None:
        if self.text is None:
            return None
        stripped = self.text.strip()[:MAX_QUERY_CHARS]
        return stripped or None


@dataclass(frozen=True, slots=True)
class RankedMemory:
    memory: Memory
    score: float


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    """Lexical retrieval over PostgreSQL full-text search.

    score = relevance * (0.5 + importance) * recency     (with a text query)
    score = importance * recency                         (without one)

    `relevance` is `ts_rank` normalised into [0, 1). Query terms are OR-ed so a
    long message still matches memories that share only some of its words.
    `recency` halves every `half_life_days`.
    """

    half_life_days: float = 30.0

    def _tsquery(self, text: str) -> ColumnElement[str]:
        # plainto_tsquery stems and escapes arbitrary input; swapping & for | gives OR semantics.
        conjunctive = cast(func.plainto_tsquery(SEARCH_CONFIG, text), String)
        return cast(func.replace(conjunctive, "&", "|"), TSQUERY)

    def _recency(self) -> ColumnElement[float]:
        age_seconds = func.extract("epoch", func.now() - Memory.created_at)
        half_lives = age_seconds / literal(self.half_life_days * SECONDS_PER_DAY)
        return cast(func.power(0.5, half_lives), Float)

    def statement(self, organization_id: uuid.UUID, query: MemoryQuery) -> Select[Memory, float]:
        recency = self._recency()
        text = query.normalized_text

        if text is None:
            score = cast(Memory.importance * recency, Float)
            statement = select(Memory, score.label("score"))
        else:
            tsquery = self._tsquery(text)
            relevance = cast(func.ts_rank(Memory.search_vector, tsquery, 32), Float)
            score = cast(relevance * (0.5 + Memory.importance) * recency, Float)
            statement = select(Memory, score.label("score")).where(
                Memory.search_vector.bool_op("@@")(tsquery)
            )

        statement = statement.where(
            Memory.organization_id == organization_id,
            Memory.deleted_at.is_(None),
            Memory.importance >= query.min_importance,
        )
        if query.types:
            statement = statement.where(Memory.type.in_(sorted(query.types)))

        return statement.order_by(score.desc(), Memory.id.desc()).limit(query.limit)
