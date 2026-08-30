"""Split book text into retrievable chunks — sections 22 and 27.

Why chunk at all
----------------
An embedding of a whole book is an average of everything it says, which is a
vector that is close to nothing in particular. §27 has to answer "a short
philosophical book that makes me question my life", and that only works if
some passage of the book is individually retrievable.

Why these boundaries
--------------------
Chunks are built from paragraphs, not from a fixed character stride. Cutting
mid-sentence produces fragments whose embeddings encode a grammatical accident
rather than a thought, and the retrieved text is then shown to a reader
starting halfway through a clause. Paragraphs are the author's own unit of
meaning; they are the right seam.

Overlap exists because the answer to a query is often the sentence that
straddles a boundary. A little repetition is much cheaper than a passage that
no chunk contains in full.

Deliberately not in here: token counting. Character budgets are approximate,
and the exactness a tokenizer buys does not change retrieval quality enough to
justify importing one — §8. The target is well inside any model's window.
"""

from __future__ import annotations

import re

# ~1,000 characters is roughly a long paragraph or two: big enough to carry an
# idea, small enough that its embedding is about one thing.
TARGET_CHARS = 1000
# Hard ceiling. A chunk over this is split even mid-paragraph, so one runaway
# wall of text cannot produce a chunk that dominates the index.
MAX_CHARS = 1500
OVERLAP_CHARS = 150
# Below this a chunk is mostly noise — a heading, a stray line — and embedding
# it adds a confident vector for nothing.
MIN_CHARS = 120

_PARAGRAPH = re.compile(r"\n\s*\n")
# Split after ., ! or ? when followed by whitespace and a capital or quote.
# Not linguistically perfect (it will cut "Dr. Smith"), but it only ever
# affects where an oversized paragraph is divided, never whether text is kept.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z])")


def _split_long(paragraph: str, limit: int) -> list[str]:
    """Break an oversized paragraph on sentence boundaries."""
    if len(paragraph) <= limit:
        return [paragraph]

    pieces: list[str] = []
    current = ""
    for sentence in _SENTENCE.split(paragraph):
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()

    if current:
        pieces.append(current)

    # A single sentence longer than the limit still has to be cut somewhere.
    # Falling back to a hard slice is correct here: the alternative is
    # silently dropping text.
    out: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            out.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            out.append(piece)
    return out


def _tail(text: str, chars: int) -> str:
    """The last `chars` of text, snapped forward to a clean boundary.

    A sentence boundary if there is one, otherwise a word boundary. Never a
    raw slice: an overlap beginning "ited for him during their happy reign"
    is not context, it is a typo the reader sees at the top of a result.
    """
    if chars <= 0 or len(text) <= chars:
        return text
    tail = text[-chars:]

    match = _SENTENCE.search(tail)
    if match:
        return tail[match.end():]

    space = tail.find(" ")
    return tail[space + 1:] if space != -1 else tail


def chunk_text(
    text: str,
    *,
    target: int = TARGET_CHARS,
    maximum: int = MAX_CHARS,
    overlap: int = OVERLAP_CHARS,
    minimum: int = MIN_CHARS,
) -> list[str]:
    """Split text into overlapping, paragraph-aligned chunks.

    Returns [] for text that has no usable content. Never returns a chunk
    longer than `maximum` (plus its overlap prefix).
    """
    if not text or not text.strip():
        return []

    paragraphs = [p.strip() for p in _PARAGRAPH.split(text) if p.strip()]
    if not paragraphs:
        return []

    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_long(paragraph, maximum))

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if not current:
            return
        body = "\n\n".join(current)
        if chunks and overlap:
            body = f"{_tail(chunks[-1], overlap)}\n\n{body}".strip()
        chunks.append(body)
        current, size = [], 0

    for unit in units:
        if current and size + len(unit) > target:
            flush()
        current.append(unit)
        size += len(unit)
    flush()

    # A trailing scrap ("THE END") is not worth its own vector; fold it back
    # into the chunk it belongs to rather than dropping the text.
    if len(chunks) > 1 and len(chunks[-1]) < minimum:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()

    return [c for c in chunks if len(c) >= minimum] or (
        [text.strip()] if len(text.strip()) >= minimum else []
    )

# --------------------------------------------------------------------------
# F-40 — front matter must not be indexed as though it were the book.
#
# `extract_reading_text` deliberately keeps title pages, contents and
# publisher notes: they are part of the opening pages a reader paginates
# through (F-17). But embedding them is a different matter. A contents block
# produces a confident vector for a book whose prose the model never saw, and
# it surfaced in practice — "a terrifying story set in a haunted house"
# returned the contents page of *The Wonder Book of Bible Stories*.
#
# Measured on 6,000 real chunks: this flags 3.4%, and spot-checking the
# flagged set found title pages, contents lists, publisher advertisements and
# transcriber credits, with roughly one preface per six wrongly caught.
#
# Thresholds are deliberately conservative. A false positive costs one chunk
# of a book that has many; a false negative puts a table of contents into
# search results. But prose does legitimately say "Chapter 4" now and then,
# so the bar is a *dense run* of enumerators, not their presence.
# --------------------------------------------------------------------------

_CONTENTS_RUN = 4
_ENUMERATION_RUN = 5
_PAGE_INDEX_RUN = 4

# The case that actually found this bug had neither "Chapter" nor a leading
# enumerator — it was a contents list with trailing page numbers:
#
#   THE STORY OF NOAH AND THE ARK 7 THE STORY OF HAGAR AND ISHMAEL 16 ...
#
# A word followed by a bare number, several times over. Prose does this
# occasionally ("in Chapter 4 he had promised"); a contents page does it in
# every line, which is what the run length distinguishes.
_PAGE_INDEX = re.compile(r"[A-Za-z]\s+\d{1,4}(?=\s|$)")


def is_front_matter(text: str) -> bool:
    """Whether a chunk is a contents block, title page or publisher note."""
    from providers.gutenberg_text import _CONTENTS, _ENUMERATION

    collapsed = " ".join(text.split())
    return (
        len(_CONTENTS.findall(collapsed)) >= _CONTENTS_RUN
        or len(_ENUMERATION.findall(collapsed)) >= _ENUMERATION_RUN
        or len(_PAGE_INDEX.findall(collapsed)) >= _PAGE_INDEX_RUN
    )
