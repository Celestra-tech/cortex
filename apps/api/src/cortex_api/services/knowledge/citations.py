"""Numbered citations: building them from retrieved passages and finding them in answers."""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

CITATION_INSTRUCTIONS = (
    "Answer using the numbered sources below. Cite every claim drawn from a source "
    "inline with its number in square brackets, e.g. [1] or [2][3]. If the sources do "
    "not contain the answer, say so instead of guessing."
)

_MARKER = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class Citation:
    index: int
    """1-based number the model is asked to cite."""
    document_id: uuid.UUID
    chunk_ids: tuple[uuid.UUID, ...]
    title: str
    source: str | None
    section: str | None
    page_start: int | None
    page_end: int | None
    score: float
    snippet: str

    @property
    def label(self) -> str:
        parts = [self.title]
        if self.section:
            headings = self.section.split(" > ")
            # A document's H1 usually repeats its title.
            if headings[0].casefold() == self.title.casefold():
                headings = headings[1:]
            parts.extend(headings)
        label = " > ".join(parts)
        if self.page_start is not None:
            pages = (
                f"p. {self.page_start}"
                if self.page_end in (None, self.page_start)
                else f"pp. {self.page_start}-{self.page_end}"
            )
            label = f"{label} ({pages})"
        return label


def cited_indices(output: str, citation_count: int) -> list[int]:
    """Citation numbers used in `output`, in first-use order, ignoring out-of-range ones."""
    seen: dict[int, None] = {}
    for match in _MARKER.finditer(output):
        for part in match.group(1).split(","):
            number = int(part)
            if 1 <= number <= citation_count:
                seen.setdefault(number, None)
    return list(seen)


def snippet(content: str, terms: Iterable[str], *, max_chars: int = 240) -> str:
    """The sentence sharing the most words with the query, trimmed at a word boundary."""
    wanted = {term.lower() for term in terms}
    sentences = [s.strip() for s in _SENTENCE.split(content) if s.strip()] or [content.strip()]

    def overlap(sentence: str) -> int:
        words = {w.lower() for w in _WORD.findall(sentence)}
        # Query lexemes are stemmed, so match on prefixes as well as whole words.
        return sum(1 for term in wanted if any(w.startswith(term) for w in words))

    best = max(sentences, key=overlap) if wanted else sentences[0]
    if len(best) <= max_chars:
        return best
    cut = best[: max_chars - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:") + "…"


def render_sources(blocks: Sequence[tuple[Citation, str]]) -> str:
    """The context block a model sees: numbered, labelled passages."""
    return "\n\n".join(f"[{citation.index}] {citation.label}\n{text}" for citation, text in blocks)
