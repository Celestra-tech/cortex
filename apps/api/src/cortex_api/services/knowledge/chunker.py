"""Structure-aware semantic chunking.

1. **Sections.** Markdown headings (DOCX headings are converted to them) split
   the text into sections. A chunk never spans two sections, and each chunk
   records its heading path, e.g. "Billing > Refunds". A heading with no body
   is folded into the section that follows it.
2. **Pieces.** A section that exceeds the chunk size is split recursively on
   the separators in priority order: paragraphs, then lines, then sentences,
   then clauses, then words. A piece that is still too large after the last
   separator is cut by length.
3. **Packing.** Consecutive pieces are packed greedily up to the chunk size.
   Each new chunk starts with up to `chunk_overlap` tokens from the end of the
   previous chunk, cut at a sentence or word boundary, so an idea that straddles
   a boundary is whole in at least one chunk.

Chunk content is always the exact span `text[char_start:char_end]`, so
citations can point back to the source and adjacent chunks can be merged
without duplicating their overlap.
"""

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from cortex_api.services.memory.encoder import HeuristicTokenEstimator, TokenEstimator

DEFAULT_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", "? ", "! ", "; ", ": ", ", ", " ")

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_OVERLAP_BOUNDARY = re.compile(r"(?<=[.!?;:])\s+|\n+")
_WORD_BOUNDARY = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    chunk_size: int = 512
    """Target maximum tokens per chunk."""
    chunk_overlap: int = 64
    """Tokens repeated from the end of the previous chunk in the same section."""
    separators: tuple[str, ...] = DEFAULT_SEPARATORS
    min_chunk_tokens: int = 24
    """A trailing chunk smaller than this is merged into its predecessor when it fits."""

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_overlap must be at least 0 and smaller than chunk_size")
        if not self.separators or any(not s for s in self.separators):
            raise ValueError("separators must be non-empty strings")


@dataclass(frozen=True, slots=True)
class Chunk:
    index: int
    content: str
    token_count: int
    char_start: int
    char_end: int
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True, slots=True)
class _Section:
    start: int
    end: int
    path: str | None


@dataclass(slots=True)
class _Span:
    start: int
    end: int


@dataclass(slots=True)
class SemanticChunker:
    config: ChunkingConfig = field(default_factory=ChunkingConfig)
    estimator: TokenEstimator = field(default_factory=HeuristicTokenEstimator)

    def chunk(self, text: str, *, page_offsets: Sequence[int] | None = None) -> list[Chunk]:
        spans: list[tuple[_Span, str | None]] = []
        for section in _sections(text):
            for span in self._pack(text, section):
                spans.append((span, section.path))

        chunks: list[Chunk] = []
        for span, path in spans:
            content = text[span.start : span.end]
            pages = _page_span(page_offsets, span) if page_offsets else (None, None)
            chunks.append(
                Chunk(
                    index=len(chunks),
                    content=content,
                    token_count=self.estimator.count(content),
                    char_start=span.start,
                    char_end=span.end,
                    section=path,
                    page_start=pages[0],
                    page_end=pages[1],
                )
            )
        return chunks

    def _tokens(self, text: str, span: _Span) -> int:
        return self.estimator.count(text[span.start : span.end])

    def _pack(self, text: str, section: _Section) -> list[_Span]:
        size = self.config.chunk_size
        pieces = self._split(text, _trim(text, _Span(section.start, section.end)), 0)
        chunks: list[_Span] = []
        current: _Span | None = None
        for piece in pieces:
            if current is None:
                current = _Span(piece.start, piece.end)
                continue
            if self._tokens(text, _Span(current.start, piece.end)) <= size:
                current.end = piece.end
                continue
            chunks.append(current)
            overlap_start = self._overlap_start(text, current)
            # Overlap is dropped when it would leave no room for the new piece.
            if (
                overlap_start is not None
                and self._tokens(text, _Span(overlap_start, piece.end)) <= size
            ):
                current = _Span(overlap_start, piece.end)
            else:
                current = _Span(piece.start, piece.end)
        if current is not None:
            chunks.append(current)
        return self._merge_small_tail(text, chunks)

    def _split(self, text: str, span: _Span, level: int) -> list[_Span]:
        if span.end <= span.start:
            return []
        if self._tokens(text, span) <= self.config.chunk_size:
            return [span]
        if level >= len(self.config.separators):
            return self._split_by_length(text, span)

        separator = self.config.separators[level]
        pieces: list[_Span] = []
        cursor = span.start
        while cursor < span.end:
            found = text.find(separator, cursor, span.end)
            # The separator stays with the left piece so spans remain contiguous.
            end = span.end if found == -1 else found + len(separator)
            piece = _trim(text, _Span(cursor, end))
            if piece.end > piece.start:
                pieces.extend(self._split(text, piece, level + 1))
            cursor = end
        return pieces

    def _split_by_length(self, text: str, span: _Span) -> list[_Span]:
        pieces: list[_Span] = []
        start = span.start
        while start < span.end:
            remaining = _Span(start, span.end)
            tokens = self._tokens(text, remaining)
            if tokens <= self.config.chunk_size:
                pieces.append(remaining)
                break
            width = max(1, (span.end - start) * self.config.chunk_size // tokens)
            end = start + width
            while (
                end > start + 1 and self._tokens(text, _Span(start, end)) > self.config.chunk_size
            ):
                end -= max(1, (end - start) // 10)
            pieces.append(_Span(start, end))
            start = end
        return pieces

    def _overlap_start(self, text: str, chunk: _Span) -> int | None:
        """Earliest sentence (else word) boundary whose suffix fits in the overlap budget."""
        budget = self.config.chunk_overlap
        if budget == 0:
            return None
        for pattern in (_OVERLAP_BOUNDARY, _WORD_BOUNDARY):
            for match in pattern.finditer(text, chunk.start, chunk.end):
                start = match.end()
                if start >= chunk.end:
                    break
                if self._tokens(text, _Span(start, chunk.end)) <= budget:
                    return start
        return None

    def _merge_small_tail(self, text: str, chunks: list[_Span]) -> list[_Span]:
        if len(chunks) < 2:
            return chunks
        tail, previous = chunks[-1], chunks[-2]
        merged = _Span(previous.start, tail.end)
        if (
            self._tokens(text, tail) < self.config.min_chunk_tokens
            and self._tokens(text, merged) <= self.config.chunk_size
        ):
            return [*chunks[:-2], merged]
        return chunks


def _trim(text: str, span: _Span) -> _Span:
    start, end = span.start, span.end
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return _Span(start, end)


def _sections(text: str) -> list[_Section]:
    """Splits on Markdown headings outside code fences, tracking the heading path."""
    boundaries: list[tuple[int, str | None]] = [(0, None)]
    stack: list[tuple[int, str]] = []
    in_fence = False
    offset = 0
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (heading := _HEADING.match(line.rstrip("\n"))):
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
            boundaries.append((offset, " > ".join(title for _, title in stack)))
        offset += len(line)

    sections: list[_Section] = []
    pending_start: int | None = None
    for i, (start, path) in enumerate(boundaries):
        end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
        body = text[start:end]
        if not body.strip():
            continue
        first_line, _, rest = body.partition("\n")
        heading_only = path is not None and _HEADING.match(first_line) and not rest.strip()
        if heading_only and i + 1 < len(boundaries):
            pending_start = start if pending_start is None else pending_start
            continue
        sections.append(_Section(start if pending_start is None else pending_start, end, path))
        pending_start = None
    return sections


def _page_span(page_offsets: Sequence[int], span: _Span) -> tuple[int, int]:
    first = bisect.bisect_right(page_offsets, span.start)
    last = bisect.bisect_right(page_offsets, span.end - 1)
    return max(first, 1), max(last, 1)
