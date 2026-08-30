"""F-39 — a backend switch must not leave two vector spaces in one column.

Selecting only NULL vectors was not enough. After a backend change the old
rows keep their old vectors while new ones get the new model, and cosine
similarity between two different vector spaces is meaningless. Search degrades
while every individual row still looks correctly "embedded" — the worst shape
of bug, because nothing looks wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)


_CONTENT = "a paragraph of prose long enough to embed meaningfully"


@pytest.fixture
def stale_chunk():
    """A chunk carrying a vector from a backend we are no longer using."""
    import embed_pass  # noqa: F401  (import guard: module must load)
    from db import SessionLocal
    from models import Book, BookChunk

    with SessionLocal() as session:
        book = Book(
            source="test", external_id="f39-fixture", title="F-39 Fixture",
            author="Test", language="en",
        )
        session.add(book)
        session.flush()
        chunk = BookChunk(
            book_id=book.id, origin="text", ordinal=0,
            content=_CONTENT,
            char_count=len(_CONTENT),
            embedding=[0.1] * 384,
            embedding_model="a-backend-we-no-longer-use",
        )
        session.add(chunk)
        session.commit()
        ids = (book.id, chunk.id)

    yield ids

    with SessionLocal() as session:
        session.query(BookChunk).filter(BookChunk.id == ids[1]).delete()
        session.query(Book).filter(Book.id == ids[0]).delete()
        session.commit()


def test_a_chunk_from_another_backend_is_re_embedded(stale_chunk):
    """Not --redo. An ordinary run must repair the mismatch on its own."""
    import embed_pass
    from db import SessionLocal
    from models import BookChunk

    _, chunk_id = stale_chunk
    embed_pass.run(limit=5000, redo=False, backend_name="hashing")

    with SessionLocal() as session:
        refreshed = session.get(BookChunk, chunk_id)
        assert refreshed.embedding_model == "hashing", (
            "a chunk carrying another backend's vector was left alone — the "
            "corpus is now a mix of two vector spaces"
        )


def test_a_chunk_already_on_the_current_backend_is_left_alone(stale_chunk):
    """The fix must not turn every run into a full re-embed."""
    import embed_pass
    from db import SessionLocal
    from models import BookChunk

    _, chunk_id = stale_chunk
    embed_pass.run(limit=5000, redo=False, backend_name="hashing")
    second = embed_pass.run(limit=5000, redo=False, backend_name="hashing")

    assert second["embedded"] == 0, (
        f"re-embedded {second['embedded']} chunk(s) that were already current"
    )
    with SessionLocal() as session:
        assert session.get(BookChunk, chunk_id).embedding_model == "hashing"


def test_health_names_a_backend_mismatch_instead_of_leaving_search_silent(stale_chunk):
    """F-39's other half.

    `search_books` filters chunks to the query's own vector space, so a
    mismatch returns an empty list rather than nonsense — safe, but baffling:
    every query answers 200 with nothing and nothing says why. /health must
    name it.
    """
    import main

    class FakeEncoder:
        name = "a-backend-nothing-was-embedded-with"

    original = main._SEARCH_ENCODER
    try:
        main._SEARCH_ENCODER = FakeEncoder()
        health = main._search_corpus_health()
    finally:
        main._SEARCH_ENCODER = original

    if not health.get("corpus_chunks"):
        pytest.skip("no embedded chunks to compare against")
    assert health["searchable_chunks"] == 0
    assert health["corpus_backend_mismatch"] is True, (
        "an encoder that can reach no chunks was reported as healthy"
    )


def test_health_does_not_cry_mismatch_when_the_backend_matches(stale_chunk):
    """The alarm has to be quiet in the normal case to be worth anything."""
    import main

    class MatchingEncoder:
        # The fixture chunk carries exactly this model name.
        name = "a-backend-we-no-longer-use"

    original = main._SEARCH_ENCODER
    try:
        main._SEARCH_ENCODER = MatchingEncoder()
        health = main._search_corpus_health()
    finally:
        main._SEARCH_ENCODER = original

    assert health["corpus_backend_mismatch"] is False
    assert health["searchable_chunks"] > 0
