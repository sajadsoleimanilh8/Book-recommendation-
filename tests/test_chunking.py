"""Chunking tests — sections 22 and 27.

The properties that matter for retrieval are: no text is lost, no chunk is
unboundedly large, and boundaries fall where an author put them. Each is
tested directly rather than by asserting on a golden chunk list, which would
break on every tuning change without saying anything about correctness.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from chunking import MAX_CHARS, MIN_CHARS, OVERLAP_CHARS, chunk_text  # noqa: E402


def _para(word: str, n: int) -> str:
    return " ".join([word] * n)


@pytest.mark.parametrize("empty", ["", "   ", "\n\n\n", None])
def test_empty_input_yields_no_chunks(empty):
    assert chunk_text(empty or "") == []


def test_short_text_is_one_chunk():
    text = "A short but complete paragraph. " * 6
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0].strip() == text.strip()


def test_no_chunk_exceeds_the_maximum_plus_its_overlap():
    text = "\n\n".join(_para("word", 400) for _ in range(10))
    for chunk in chunk_text(text):
        assert len(chunk) <= MAX_CHARS + OVERLAP_CHARS + 10


def test_no_text_is_lost():
    """Every word of the source must survive into at least one chunk.

    This is the property that a fixed-stride splitter gets wrong at the tail,
    and losing the last paragraph of a book is invisible until someone
    searches for it.
    """
    paragraphs = [f"Paragraph {i} carries a unique marker word zebra{i}. " * 8 for i in range(12)]
    text = "\n\n".join(paragraphs)
    joined = " ".join(chunk_text(text))
    for i in range(12):
        assert f"zebra{i}" in joined, f"lost paragraph {i}"


def test_chunks_overlap_so_a_straddling_sentence_survives():
    text = "\n\n".join(_para(f"body{i}", 120) for i in range(6))
    chunks = chunk_text(text)
    assert len(chunks) > 1
    # Each chunk after the first should begin with material from its
    # predecessor, or a sentence spanning the seam is retrievable from neither.
    for previous, following in zip(chunks, chunks[1:]):
        head = following[:OVERLAP_CHARS].strip().split()
        assert head, "empty overlap prefix"
        assert head[0] in previous, "chunk does not carry any preceding context"


def test_paragraph_boundaries_are_preferred_over_mid_sentence_cuts():
    text = "\n\n".join(f"Paragraph number {i} is entirely self contained." for i in range(40))
    for chunk in chunk_text(text):
        # Nothing should end mid-word.
        assert not chunk.rstrip().endswith(("Paragrap", "numbe", "entirel"))


def test_a_single_enormous_sentence_is_still_split():
    """No sentence boundary to use, so a hard cut is correct — the wrong
    answer is dropping the text or emitting one vast chunk."""
    text = "word " * 3000  # ~15,000 chars, no paragraph or sentence breaks
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHARS + OVERLAP_CHARS + 10 for c in chunks)


def test_tiny_trailing_scrap_is_folded_back_not_dropped():
    text = "\n\n".join(_para("content", 200) for _ in range(3)) + "\n\nTHE END"
    chunks = chunk_text(text)
    assert any("THE END" in c for c in chunks), "trailing text was dropped"
    assert all(len(c) >= MIN_CHARS for c in chunks)


def test_real_gutenberg_prose_chunks_sensibly():
    """Guards against tuning that looks fine on synthetic input.

    Real prose has short dialogue paragraphs mixed with long descriptive ones,
    which is exactly what breaks naive accumulate-until-full splitters.
    """
    prose = (
        'It is a truth universally acknowledged, that a single man in '
        "possession of a good fortune, must be in want of a wife.\n\n"
        '"My dear Mr. Bennet," said his lady to him one day, "have you heard '
        'that Netherfield Park is let at last?"\n\n'
        "Mr. Bennet replied that he had not.\n\n"
        '"But it is," returned she; "for Mrs. Long has just been here, and '
        'she told me all about it."\n\n'
    ) * 6

    chunks = chunk_text(prose)
    assert len(chunks) > 1
    assert all(len(c) >= MIN_CHARS for c in chunks)
    assert "universally acknowledged" in chunks[0]


def test_tail_snaps_to_a_word_boundary_when_there_is_no_sentence_break():
    """The overlap prefix must never begin mid-word.

    Real Gutenberg text produced 'ited for him during their happy reign' at
    the top of a chunk — the tail of "awaited", sliced blind. A 150-character
    window through a long paragraph frequently contains no sentence break at
    all, so the sentence-boundary path does not save it.

    Tested against `_tail` directly: at the chunk level, whether a fixed-size
    slice happens to land on a boundary depends on the arithmetic of the
    fixture, so a passing chunk-level assertion proves nothing.
    """
    from chunking import _tail

    text = "the prince awaited his coronation with considerable impatience"
    # A blind 20-char slice gives "siderable impatience"; snapping forward
    # past the broken word is what makes the prefix readable.
    assert text[-20:] == "siderable impatience"
    assert _tail(text, 20) == "impatience"
    # No spaces at all: nothing to snap to, so returning the slice is correct.
    assert _tail("x" * 100, 10) == "x" * 10
    # Shorter than the window: unchanged.
    assert _tail("short", 50) == "short"


def test_tail_prefers_a_sentence_boundary_over_a_word_boundary():
    from chunking import _tail

    text = "He left the room. She stayed behind and considered the question."
    # The window must actually reach the sentence break for it to be used.
    assert _tail(text, 50).startswith("She stayed")
    # A window that does not reach it falls back to the word boundary.
    assert _tail(text, 40).startswith("behind")
