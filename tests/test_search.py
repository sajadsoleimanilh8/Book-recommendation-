"""Semantic search tests — sections 22 and 27.

Most of these are authorization tests, because that is where the damage is.
A ranking bug shows a reader a mediocre book; a visibility bug shows them
somebody else's private library.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import select

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)

from db import SessionLocal  # noqa: E402
from embeddings import get_backend  # noqa: E402
from models import Book, BookChunk, User  # noqa: E402
from search import search_books, search_chunks, visible_chunks  # noqa: E402

ENCODER = get_backend("hashing")

PUBLIC_TEXT = "whales harpoons and the wide grey ocean under a whaling captain"
ALICE_TEXT = "alice private notebook concerning her own uploaded manuscript"
BOB_TEXT = "bob private ledger concerning his own uploaded manuscript"


def _vec(text: str):
    return ENCODER.encode([text])[0].tolist()


@pytest.fixture
def corpus():
    """Two users, one book, three chunks: one public and one private each."""
    with SessionLocal() as session:
        book = session.scalar(select(Book).limit(1))
        assert book is not None, "test catalogue is empty"
        book_id = book.id

        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))

        users = {}
        for name in ("alice", "bob"):
            user = session.scalar(select(User).where(User.email == f"{name}.chunks@example.com"))
            if user is None:
                user = User(email=f"{name}.chunks@example.com", password_hash="x")
                session.add(user)
                session.flush()
            users[name] = user.id

        session.add_all(
            [
                BookChunk(
                    book_id=book_id, user_id=None, visibility="public", ordinal=0,
                    content=PUBLIC_TEXT, char_count=len(PUBLIC_TEXT),
                    embedding=_vec(PUBLIC_TEXT), embedding_model="hashing",
                ),
                BookChunk(
                    book_id=book_id, user_id=users["alice"], visibility="private", ordinal=1,
                    content=ALICE_TEXT, char_count=len(ALICE_TEXT),
                    embedding=_vec(ALICE_TEXT), embedding_model="hashing",
                ),
                BookChunk(
                    book_id=book_id, user_id=users["bob"], visibility="private", ordinal=2,
                    content=BOB_TEXT, char_count=len(BOB_TEXT),
                    embedding=_vec(BOB_TEXT), embedding_model="hashing",
                ),
            ]
        )
        session.commit()
        yield {"book_id": book_id, **users}

        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))
        session.commit()


def _passages(hits):
    return {h.passage for h in hits}


# -- authorization ---------------------------------------------------------


def test_anonymous_search_sees_only_public_chunks(corpus):
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(ALICE_TEXT), user_id=None, limit=50)
        assert all(h.visibility == "public" for h in hits)
        assert ALICE_TEXT not in _passages(hits)
        assert BOB_TEXT not in _passages(hits)


def test_a_user_never_sees_another_users_private_chunk(corpus):
    """The single most important assertion in this file.

    The query is Bob's own text, so his chunk is the nearest neighbour by a
    wide margin. If the filter is wrong, Alice gets it first.
    """
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(BOB_TEXT), user_id=corpus["alice"], limit=50)
        assert BOB_TEXT not in _passages(hits), "leaked another user's private chunk"


def test_a_user_does_see_their_own_private_chunk(corpus):
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(ALICE_TEXT), user_id=corpus["alice"], limit=50)
        assert ALICE_TEXT in _passages(hits)


def test_signed_in_users_still_see_public_chunks(corpus):
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(PUBLIC_TEXT), user_id=corpus["alice"], limit=50)
        assert PUBLIC_TEXT in _passages(hits)


def test_anonymous_predicate_is_not_a_null_comparison():
    """`user_id == None` renders as IS NULL, which matches every public row
    through the private branch. Same answer today, a hole the moment the
    branch changes — so the anonymous case must compile to a plain filter on
    the public condition, with no OR arm.
    """
    sql = str(visible_chunks(None))
    assert "OR" not in sql.upper()
    assert str(visible_chunks(7)).upper().count("OR") == 1


# -- retrieval behaviour ---------------------------------------------------


def test_results_are_ordered_by_similarity(corpus):
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(PUBLIC_TEXT), user_id=corpus["alice"], limit=50)
        scores = [h.similarity for h in hits]
        assert scores == sorted(scores, reverse=True)


def test_every_result_is_a_real_row_never_generated(corpus):
    """Section 27: the system must not invent books."""
    with SessionLocal() as session:
        hits = search_chunks(session, _vec(PUBLIC_TEXT), limit=50)
        assert hits
        for hit in hits:
            row = session.get(BookChunk, hit.chunk_id)
            assert row is not None
            assert row.content == hit.passage
            assert session.get(Book, hit.book_id) is not None


def test_nonsense_query_returns_nothing_rather_than_a_bad_guess(corpus):
    """Returning nothing is the correct failure. A confident irrelevant
    result is worse than an empty list, because the reader cannot tell."""
    with SessionLocal() as session:
        hits = search_chunks(
            session, _vec("zzzqqq xyzzy plugh frobnicate"), limit=50, min_similarity=0.05
        )
        assert hits == []


def test_book_level_search_collapses_duplicate_books(corpus):
    """Six passages from one novel must not fill the whole first page."""
    with SessionLocal() as session:
        rows = search_books(session, _vec(PUBLIC_TEXT), user_id=corpus["alice"], limit=10)
        book_ids = [r["book_id"] for r in rows]
        assert len(book_ids) == len(set(book_ids))


def test_limit_is_clamped_not_trusted(corpus):
    with SessionLocal() as session:
        assert len(search_chunks(session, _vec(PUBLIC_TEXT), limit=10_000)) <= 50
        assert len(search_chunks(session, _vec(PUBLIC_TEXT), limit=0)) <= 1


def test_metadata_filters_narrow_results(corpus):
    with SessionLocal() as session:
        book = session.get(Book, corpus["book_id"])
        unmatched = search_chunks(
            session, _vec(PUBLIC_TEXT), limit=50, language="zz-nonexistent"
        )
        assert unmatched == []

        if book.language:
            matched = search_chunks(
                session, _vec(PUBLIC_TEXT), limit=50, language=book.language
            )
            assert PUBLIC_TEXT in _passages(matched)


def test_the_lsa_model_name_identifies_the_fitted_space_not_just_the_algorithm():
    """F-35's guard compares `embedding_model`. Refitting LSA on a different
    corpus produces a completely different vector space — if both were called
    "lsa", the mixed vectors would pass the very check built to catch them.

    Unlike a pretrained model, an LSA space is defined by its corpus, so the
    corpus has to be part of the identity.
    """
    import numpy as np

    from embeddings import LsaBackend

    # Varied enough that max_df pruning leaves a vocabulary behind, the way a
    # real corpus of book passages does.
    nautical = ["sailor", "harbour", "mast", "tide", "anchor", "voyage", "storm", "keel"]
    baking = ["baker", "flour", "oven", "yeast", "crust", "dough", "loaf", "sugar"]
    corpus_a = [f"{w} {nautical[(i + 3) % 8]} chapter {i}" for i, w in
                enumerate(nautical * 6)]
    corpus_b = [f"{w} {baking[(i + 3) % 8]} chapter {i}" for i, w in
                enumerate(baking * 6)]

    a = LsaBackend(dim=8).fit(corpus_a)
    b = LsaBackend(dim=8).fit(corpus_b)

    assert a.name != b.name, "two different fitted spaces share one name"
    assert a.name.startswith("lsa:") and b.name.startswith("lsa:")

    # Refitting the same corpus must be stable, or every fit would look like a
    # new space and force a pointless full re-embed.
    again = LsaBackend(dim=8).fit(corpus_a)
    assert again.name == a.name

    # An unfitted backend has no space to identify.
    assert LsaBackend(dim=8).name == "lsa"
    assert np.isfinite(a.encode(["a ship at sea"])).all()
