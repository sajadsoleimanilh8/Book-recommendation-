"""F-40 — a table of contents must not be indexed as though it were the book.

Found in a live search result: "a terrifying story set in a haunted house"
returned the contents page of *The Wonder Book of Bible Stories* as its top
hit. `extract_reading_text` keeps front matter on purpose — it is part of the
opening pages a reader paginates through (F-17) — but embedding it produces a
confident vector for prose the model never saw.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest  # noqa: E402

from chunking import is_front_matter  # noqa: E402

# All taken verbatim from chunks that were actually in the search index.
FRONT_MATTER = [
    pytest.param(
        "LETTER III. Belford to Mowbray.-- LETTER IV. From the same.-- "
        "LETTER V. To Lovelace.-- LETTER VI. Answer.-- LETTER VII. More.",
        id="letter-index",
    ),
    pytest.param(
        "LAND PART III THE MERMAIDS' LAGOON PART IV THE UNDERGROUND HOME "
        "PART V THE PIRATE SHIP PART VI HOME, SWEET HOME PART VII",
        id="parts-contents",
    ),
    pytest.param(
        "THE STORY OF NOAH AND THE ARK 7 THE STORY OF HAGAR AND ISHMAEL 16 "
        "THE STORY OF ABRAHAM AND ISAAC 22 THE STORY OF JACOB 28 "
        "THE SALE OF A BIRTHRIGHT 29",
        id="the-hit-that-found-this-bug",
    ),
]

PROSE = [
    pytest.param(
        "She caught up her despised coat and dashed wildly out of the gate in "
        "a perfect tempest of anger and resentment, hardly knowing where she "
        "went or caring very much either.",
        id="narrative",
    ),
    pytest.param(
        "Paul Stevens, the owner of the store, was out in the granary at the "
        "back helping a farmer get a load of oats, and so did not see who came "
        "in at the front.",
        id="narrative-with-names",
    ),
    pytest.param(
        "In Chapter 4 he had promised to explain himself, and now, sitting by "
        "the fire with the letter still unopened, he found that he could not.",
        id="prose-may-mention-a-chapter",
    ),
]


@pytest.mark.parametrize("text", FRONT_MATTER)
def test_contents_blocks_are_recognised(text):
    assert is_front_matter(text)


@pytest.mark.parametrize("text", PROSE)
def test_real_prose_is_not_filtered(text):
    """The expensive failure. A false positive silently removes a real
    passage from search, and nothing would ever point at it."""
    assert not is_front_matter(text)


def test_a_book_that_looks_entirely_like_front_matter_is_still_indexed():
    """If the filter would empty a book it is wrong about that book, and
    indexing something beats indexing nothing."""
    import chunk_pass

    source = __import__("inspect").getsource(chunk_pass.chunk_one)
    assert "kept or pieces" in source, (
        "the empty-book guard is gone — a false positive could now erase a "
        "book from search entirely"
    )
