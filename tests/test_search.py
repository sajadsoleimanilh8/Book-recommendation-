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
from models import Book, BookChunk, BookVector, User  # noqa: E402
from search import search_books, search_chunks, visible_chunks  # noqa: E402

ENCODER = get_backend("hashing")

PUBLIC_TEXT = "whales harpoons and the wide grey ocean under a whaling captain"
ALICE_TEXT = "alice private notebook concerning her own uploaded manuscript"
BOB_TEXT = "bob private ledger concerning his own uploaded manuscript"


def _vec(text: str):
    return ENCODER.encode([text])[0].tolist()


@pytest.fixture
def corpus():
    """Two users, one book, three chunks: one public and one private each.

    F-64: `select(Book).limit(1)` carries no ORDER BY, so it is not
    guaranteed to land on an empty book — in this database it resolves to a
    real, fully-chunked catalogue book. Its real chunks are snapshotted
    before the delete and restored in teardown, rather than left gone.
    """
    with SessionLocal() as session:
        book = session.scalar(select(Book).limit(1))
        assert book is not None, "test catalogue is empty"
        book_id = book.id

        original = list(
            session.scalars(select(BookChunk).where(BookChunk.book_id == book_id))
        )
        original_snapshot = [
            {
                "user_id": r.user_id,
                "visibility": r.visibility,
                "ordinal": r.ordinal,
                "origin": r.origin,
                "content": r.content,
                "char_count": r.char_count,
                "embedding": r.embedding,
                "embedding_model": r.embedding_model,
            }
            for r in original
        ]
        for row in original:
            session.expunge(row)

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
        session.add_all(
            BookChunk(book_id=book_id, **fields) for fields in original_snapshot
        )
        session.commit()


def _passages(hits):
    return {h.passage for h in hits}


@pytest.fixture
def book_vector_corpus():
    """Two books: one with a chunk (findable the old way), one deliberately
    without (the case `search_books` used to be blind to, F-44's whole
    point) — both get a `book_vectors` row, since that is what book-level
    search now queries.

    `book_vectors` is not like `book_chunks`: `load_content_vectors`
    (services/store.py) is all-or-nothing — if even one book in the whole
    catalogue is missing a vector, the ML fit falls back to TF-IDF for
    *everything*, moving every golden ranking score. So the original
    `BookVector` row is snapshotted first and put back, not just deleted.

    **F-64 correction:** this docstring used to claim a delete-and-leave-empty
    teardown was safe for `book_chunks` too, "since nothing reads 'all chunks
    exist'". True of the golden suite, false in general: `select(Book).limit`
    with no `ORDER BY` is not guaranteed to land on an empty book, and here it
    resolves to a real, fully-chunked catalogue book — so that teardown was
    silently and permanently deleting real search content every run. Fixed
    below with the same snapshot-and-restore discipline as `BookVector`.
    """
    with SessionLocal() as session:
        books = list(session.scalars(select(Book).limit(2)))
        assert len(books) == 2, "test catalogue needs at least two books"
        with_chunk, chunkless = books[0].id, books[1].id

        original = {
            row.book_id: row
            for row in session.scalars(
                select(BookVector).where(BookVector.book_id.in_([with_chunk, chunkless]))
            )
        }
        original_snapshot = {
            book_id: {
                "embedding": row.embedding,
                "embedding_model": row.embedding_model,
                "has_description": row.has_description,
                "source_chars": row.source_chars,
            }
            for book_id, row in original.items()
        }
        original_chunks = list(
            session.scalars(select(BookChunk).where(BookChunk.book_id == with_chunk))
        )
        original_chunks_snapshot = [
            {
                "user_id": r.user_id,
                "visibility": r.visibility,
                "ordinal": r.ordinal,
                "origin": r.origin,
                "content": r.content,
                "char_count": r.char_count,
                "embedding": r.embedding,
                "embedding_model": r.embedding_model,
            }
            for r in original_chunks
        ]
        # See test_search_endpoint.py's `seeded` fixture for why this
        # expunge matters: the bulk delete below does not expire these
        # already-loaded ORM objects from the identity map, so re-adding a
        # BookVector for the same book_id later raises a SQLAlchemy warning.
        for row in original.values():
            session.expunge(row)
        for row in original_chunks:
            session.expunge(row)

        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == with_chunk))
        session.execute(BookVector.__table__.delete().where(
            BookVector.book_id.in_([with_chunk, chunkless])
        ))

        session.add(BookChunk(
            book_id=with_chunk, user_id=None, visibility="public", ordinal=0,
            content=PUBLIC_TEXT, char_count=len(PUBLIC_TEXT),
            embedding=_vec(PUBLIC_TEXT), embedding_model="hashing",
        ))
        session.add_all([
            BookVector(
                book_id=with_chunk, embedding=_vec(PUBLIC_TEXT),
                embedding_model="hashing", has_description=True,
            ),
            BookVector(
                book_id=chunkless, embedding=_vec(PUBLIC_TEXT),
                embedding_model="hashing", has_description=False,
            ),
        ])
        session.commit()
        yield {"with_chunk": with_chunk, "chunkless": chunkless}

        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == with_chunk))
        session.execute(BookVector.__table__.delete().where(
            BookVector.book_id.in_([with_chunk, chunkless])
        ))
        for book_id, fields in original_snapshot.items():
            session.add(BookVector(book_id=book_id, **fields))
        session.add_all(
            BookChunk(book_id=with_chunk, **fields) for fields in original_chunks_snapshot
        )
        session.commit()


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
    result is worse than an empty list, because the reader cannot tell.

    F-64: `search_chunks` has no way to scope a query to one book, and this
    ran unscoped against the whole shared `book_chunks` table. Against a
    real, tens-of-thousands-of-chunks catalogue a nonsense query can
    legitimately clear `min_similarity=0.05` against *something* real
    somewhere — that is not a search-quality bug, it is a big corpus. The
    claim this test actually makes is about this fixture's own synthetic
    content, so the assertion is scoped to it.
    """
    with SessionLocal() as session:
        hits = search_chunks(
            session, _vec("zzzqqq xyzzy plugh frobnicate"), limit=50, min_similarity=0.05
        )
        own_hits = [h for h in hits if h.book_id == corpus["book_id"]]
        assert own_hits == []


def test_book_level_search_returns_each_book_once(book_vector_corpus):
    """`book_vectors` is one row per book by construction (primary key), but
    prove it rather than trust the schema silently."""
    with SessionLocal() as session:
        rows = search_books(
            session, _vec(PUBLIC_TEXT), limit=10, embedding_model="hashing"
        )
        book_ids = [r["book_id"] for r in rows]
        assert len(book_ids) == len(set(book_ids))


def test_book_level_search_finds_a_book_with_no_chunk_at_all(book_vector_corpus):
    """F-44's whole point, proven directly: the old `search_books` (collapsed
    `search_chunks`) could only ever find the ~23% of the catalogue with a
    chunk — a book with no description and no stored text was invisible to
    it regardless of how well it matched, silently, since an empty result
    looks identical to "nothing matched." `book_vectors` covers every book,
    so the chunkless one must be findable too."""
    with SessionLocal() as session:
        rows = search_books(
            session, _vec(PUBLIC_TEXT), limit=10, min_similarity=0.0,
            embedding_model="hashing",
        )
        book_ids = {r["book_id"] for r in rows}
        assert book_vector_corpus["chunkless"] in book_ids, (
            "a book with no chunk was invisible to book-level search"
        )
        assert book_vector_corpus["with_chunk"] in book_ids


def test_book_level_search_reports_whether_the_vector_saw_a_description(book_vector_corpus):
    """`has_description` is what lets a caller (or an LLM grounding on this)
    tell a vector built from real description text apart from one built from
    title/author/genre alone."""
    with SessionLocal() as session:
        rows = search_books(
            session, _vec(PUBLIC_TEXT), limit=10, min_similarity=0.0,
            embedding_model="hashing",
        )
        by_id = {r["book_id"]: r for r in rows}
        assert by_id[book_vector_corpus["with_chunk"]]["has_description"] is True
        assert by_id[book_vector_corpus["chunkless"]]["has_description"] is False


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
