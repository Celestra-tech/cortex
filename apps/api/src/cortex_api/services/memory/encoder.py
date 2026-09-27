import math
import re
from typing import Protocol

from cortex_api.models.message import Message
from cortex_api.schemas.memory import SessionMessage

_WHITESPACE = re.compile(r"\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s")
_ELLIPSIS = "…"


class TokenEstimator(Protocol):
    def count(self, text: str) -> int: ...


class HeuristicTokenEstimator:
    """Approximates BPE token counts at ~4 characters per token for English.

    Good enough for budgeting a context window; swap in a model-specific
    tokenizer through `TokenEstimator` when exact counts matter.
    """

    def __init__(self, chars_per_token: float = 4.0) -> None:
        self.chars_per_token = chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / self.chars_per_token))


class MemoryEncoder:
    """Turns raw text and ORM rows into the compact forms the memory layer stores."""

    def __init__(
        self, estimator: TokenEstimator | None = None, *, summary_max_chars: int = 280
    ) -> None:
        self.estimator = estimator or HeuristicTokenEstimator()
        self.summary_max_chars = summary_max_chars

    def count_tokens(self, text: str) -> int:
        return self.estimator.count(text)

    def summarize(self, content: str) -> str:
        """Extractive summary: the first sentence, or a word-boundary truncation."""
        normalized = _WHITESPACE.sub(" ", content).strip()
        if len(normalized) <= self.summary_max_chars:
            return normalized

        first_sentence = _SENTENCE_BOUNDARY.split(normalized, maxsplit=1)[0]
        if len(first_sentence) <= self.summary_max_chars:
            return first_sentence

        cut = normalized[: self.summary_max_chars - len(_ELLIPSIS)]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.rstrip(" ,;:") + _ELLIPSIS

    def to_session_message(self, message: Message) -> SessionMessage:
        return SessionMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            token_count=message.token_count,
            created_at=message.created_at,
        )
