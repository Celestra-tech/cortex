from itertools import pairwise

import pytest

from cortex_api.services.knowledge.chunker import (
    DEFAULT_SEPARATORS,
    Chunk,
    ChunkingConfig,
    SemanticChunker,
)
from cortex_api.services.memory.encoder import HeuristicTokenEstimator

ESTIMATOR = HeuristicTokenEstimator()


def chunker(size: int = 40, overlap: int = 8, **kwargs: object) -> SemanticChunker:
    return SemanticChunker(ChunkingConfig(chunk_size=size, chunk_overlap=overlap, **kwargs))  # type: ignore[arg-type]


def sentences(n: int, prefix: str = "Sentence") -> str:
    return " ".join(f"{prefix} number {i} talks about topic {i}." for i in range(n))


def assert_spans_exact(text: str, chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert chunk.content == text[chunk.char_start : chunk.char_end]
        assert chunk.content == chunk.content.strip()
        assert chunk.token_count == ESTIMATOR.count(chunk.content)


def test_short_text_is_one_chunk() -> None:
    text = "A short note."
    [chunk] = chunker().chunk(text)
    assert chunk == Chunk(0, text, ESTIMATOR.count(text), 0, len(text))


def test_empty_text_has_no_chunks() -> None:
    assert chunker().chunk("") == []
    assert chunker().chunk("   \n\n ") == []


def test_chunks_respect_size_and_cover_the_text() -> None:
    text = "\n\n".join(sentences(4, f"Para{p}") for p in range(6))
    chunks = chunker(size=60, overlap=10).chunk(text)
    assert len(chunks) > 3
    assert all(c.token_count <= 60 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert_spans_exact(text, chunks)
    # Every character of content is inside some chunk.
    covered: set[int] = set()
    for c in chunks:
        covered.update(range(c.char_start, c.char_end))
    assert all(i in covered for i, ch in enumerate(text) if not ch.isspace())


def test_overlap_repeats_the_previous_chunk_tail_at_a_sentence_boundary() -> None:
    text = sentences(12)
    chunks = chunker(size=50, overlap=15).chunk(text)
    assert len(chunks) >= 3
    for previous, current in pairwise(chunks):
        assert current.char_start < previous.char_end, "chunks should overlap"
        shared = text[current.char_start : previous.char_end]
        assert ESTIMATOR.count(shared) <= 15
        assert current.content.startswith("Sentence number")  # starts on a sentence


def test_zero_overlap_produces_disjoint_chunks() -> None:
    chunks = chunker(size=50, overlap=0).chunk(sentences(12))
    for previous, current in pairwise(chunks):
        assert current.char_start >= previous.char_end


def test_sections_are_never_crossed_and_carry_heading_paths() -> None:
    text = (
        "# Billing\n\nIntro to billing.\n\n"
        "## Refunds\n\nRefunds take five business days.\n\n"
        "## Invoices\n\nInvoices are issued monthly.\n\n"
        "# Security\n\nWe encrypt data at rest."
    )
    chunks = chunker(size=200).chunk(text)
    assert [(c.section, c.content.splitlines()[-1]) for c in chunks] == [
        ("Billing", "Intro to billing."),
        ("Billing > Refunds", "Refunds take five business days."),
        ("Billing > Invoices", "Invoices are issued monthly."),
        ("Security", "We encrypt data at rest."),
    ]
    assert chunks[1].content.startswith("## Refunds")
    assert_spans_exact(text, chunks)


def test_heading_without_body_folds_into_the_next_section() -> None:
    text = "# Guide\n\n## Setup\n\nInstall the CLI."
    [chunk] = chunker().chunk(text)
    assert chunk.section == "Guide > Setup"
    assert chunk.content == text


def test_headings_inside_code_fences_are_ignored() -> None:
    text = "# Real\n\n```bash\n# not a heading\necho hi\n```\n\nAfter the fence."
    [chunk] = chunker(size=200).chunk(text)
    assert chunk.section == "Real"


def test_text_before_the_first_heading_has_no_section() -> None:
    text = "Preamble paragraph.\n\n# Chapter\n\nBody."
    chunks = chunker().chunk(text)
    assert [c.section for c in chunks] == [None, "Chapter"]


def test_unbroken_text_is_cut_by_length() -> None:
    text = "x" * 1000
    chunks = chunker(size=50, overlap=0).chunk(text)
    assert "".join(c.content for c in chunks) == text
    assert all(c.token_count <= 50 for c in chunks)


def test_small_tail_merges_into_previous_chunk() -> None:
    body = sentences(3)
    text = f"{body}\n\nok"
    config = ChunkingConfig(chunk_size=ESTIMATOR.count(text) + 2, chunk_overlap=0)
    [chunk] = SemanticChunker(config).chunk(text)
    assert chunk.content.endswith("ok")


def test_page_spans_follow_offsets() -> None:
    pages = [sentences(3, "Alpha"), sentences(3, "Beta"), sentences(3, "Gamma")]
    text = "\n\n".join(pages)
    offsets = (0, len(pages[0]) + 2, len(pages[0]) + len(pages[1]) + 4)
    chunks = chunker(size=45, overlap=0).chunk(text, page_offsets=offsets)
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 3
    for chunk in chunks:
        assert chunk.page_start is not None and chunk.page_end is not None
        assert 1 <= chunk.page_start <= chunk.page_end <= 3
        if "Beta" in chunk.content and "Alpha" not in chunk.content:
            assert chunk.page_start == 2


def test_custom_separators() -> None:
    text = "alpha|beta|gamma|delta"
    chunks = SemanticChunker(
        ChunkingConfig(chunk_size=2, chunk_overlap=0, separators=("|",), min_chunk_tokens=0)
    ).chunk(text)
    assert [c.content for c in chunks] == ["alpha|", "beta|", "gamma|", "delta"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_size": 0},
        {"chunk_size": 10, "chunk_overlap": 10},
        {"chunk_overlap": -1},
        {"separators": ()},
        {"separators": ("\n", "")},
    ],
)
def test_invalid_config(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        ChunkingConfig(**kwargs)  # type: ignore[arg-type]


def test_defaults() -> None:
    config = ChunkingConfig()
    assert (config.chunk_size, config.chunk_overlap) == (512, 64)
    assert config.separators == DEFAULT_SEPARATORS
