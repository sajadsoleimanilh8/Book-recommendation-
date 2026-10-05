"""Priming the book-pk cache — F-20, the 15 seconds that were not the vectors.

F-20's scope attributed 16.5s of a 40.0s boot to "reading the content vectors
out of Postgres" and proposed caching the assembled matrix as an artefact.
Splitting that stage found the vectors themselves take 1.6s. The other 15.0s
was `resolve_book_pk` called once per book against an empty `_pk_cache`:
29,975 round trips of `SELECT books.id WHERE source = ? AND external_id = ?`.

So the fix is one query for every key rather than a 46 MB artefact with an
invalidation problem. These tests pin the only thing that could go wrong with
that: the primed cache resolving a book to a different row than the per-row
query would have, which would silently pair books with the wrong vectors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from conftest import database_reachable

    HAVE_DB = database_reachable()
except Exception:  # pragma: no cover
    HAVE_DB = False

pytestmark = pytest.mark.skipif(not HAVE_DB, reason="database unavailable")


@pytest.fixture
def books():
    from services.catalogue import load_books_raw

    return load_books_raw(limit=None)


def test_priming_resolves_every_book_to_the_same_row(books):
    """The whole correctness question. A mismatch here pairs a book with
    another book's vector — no error, just quietly wrong neighbours."""
    from db import SessionLocal
    from services import store

    with SessionLocal() as session:
        store._pk_cache.clear()
        per_row = [store.resolve_book_pk(session, b) for b in books]

        store._pk_cache.clear()
        store.prime_pk_cache(session)
        primed = [store.resolve_book_pk(session, b) for b in books]

    mismatches = [
        (b.get("title"), a, p)
        for b, a, p in zip(books, per_row, primed)
        if a != p
    ]
    assert not mismatches, f"{len(mismatches)} books resolved differently: {mismatches[:3]}"


def test_priming_makes_the_loop_issue_no_queries(books):
    """The point of the change, measured as queries rather than seconds —
    seconds vary by machine, a query count does not."""
    from sqlalchemy import event

    from db import SessionLocal, engine
    from services import store

    with SessionLocal() as session:
        store._pk_cache.clear()
        store.prime_pk_cache(session)

        statements = []

        def count(conn, cursor, statement, params, context, executemany):
            statements.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        try:
            for b in books:
                store.resolve_book_pk(session, b)
        finally:
            event.remove(engine, "before_cursor_execute", count)

    assert len(statements) == 0, (
        f"resolving {len(books):,} primed books issued {len(statements)} queries; "
        "the N+1 is back"
    )


def test_a_key_missing_after_priming_still_falls_through(books):
    """Priming must never declare the cache complete. A row added while the
    process runs has to be found by the per-row query, not reported missing
    because it was not there at boot."""
    from db import SessionLocal
    from services import store

    target = books[0]
    with SessionLocal() as session:
        store._pk_cache.clear()
        store.prime_pk_cache(session)
        expected = store.resolve_book_pk(session, target)

        # Simulate "not present at prime time".
        for key in [k for k, v in store._pk_cache.items() if v == expected]:
            del store._pk_cache[key]

        assert store.resolve_book_pk(session, target) == expected


def test_load_content_vectors_is_unchanged(books):
    """End to end: the matrix the recommender receives is the same shape,
    complete, and from one embedding model."""
    from db import SessionLocal
    from services import store

    store._pk_cache.clear()
    with SessionLocal() as session:
        vectors, report = store.load_content_vectors(session, books)

    assert vectors is not None, report
    assert vectors.shape == (len(books), 384)
    assert report["missing"] == 0
    assert len(report["models"]) == 1
