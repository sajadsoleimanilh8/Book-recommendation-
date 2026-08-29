"""Chunk population tests — sections 22 and 27.

The property that carries real risk here is not the chunk count. It is that
`chunk_one` clears a book's *public* chunks before rewriting them, and a
delete scoped one predicate too wide would silently destroy users' private
chunks for the same book every time the catalogue was rechunked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)

from chunk_pass import chunk_one  # noqa: E402
from db import SessionLocal  # noqa: E402
from models import Book, BookChunk, User  # noqa: E402

PROSE = (
    "It is a truth universally acknowledged, that a single man in possession "
    "of a good fortune, must be in want of a wife.\n\n"
    "However little known the feelings or views of such a man may be on his "
    "first entering a neighbourhood, this truth is so well fixed in the minds "
    "of the surrounding families, that he is considered as the rightful "
    "property of some one or other of their daughters.\n\n"
) * 8


@pytest.fixture
def sample_book():
    with SessionLocal() as session:
        book = session.scalar(select(Book).limit(1))
        assert book is not None, "test catalogue is empty"
        book_id = book.id
        session.execute(
            BookChunk.__table__.delete().where(BookChunk.book_id == book_id)
        )
        session.commit()
    yield book_id
    with SessionLocal() as session:
        session.execute(
            BookChunk.__table__.delete().where(BookChunk.book_id == book_id)
        )
        session.commit()


def _count(session, book_id, visibility):
    return session.scalar(
        select(func.count())
        .select_from(BookChunk)
        .where(BookChunk.book_id == book_id, BookChunk.visibility == visibility)
    )


def test_chunks_are_written_as_public_and_ownerless(sample_book):
    with SessionLocal() as session:
        written = chunk_one(session, sample_book, PROSE)
        session.commit()
        assert written > 1

        rows = session.scalars(
            select(BookChunk).where(BookChunk.book_id == sample_book)
        ).all()
        assert all(r.visibility == "public" for r in rows)
        assert all(r.user_id is None for r in rows)
        assert [r.ordinal for r in rows] == list(range(len(rows)))
        assert all(r.embedding is None for r in rows), "embedding is a later pass"


def test_rechunking_replaces_rather_than_duplicates(sample_book):
    with SessionLocal() as session:
        first = chunk_one(session, sample_book, PROSE)
        session.commit()
        second = chunk_one(session, sample_book, PROSE)
        session.commit()

        assert first == second
        assert _count(session, sample_book, "public") == first


def test_rechunking_the_catalogue_never_touches_private_chunks(sample_book):
    """The regression that would matter most.

    `chunk_one` deletes before it inserts. If that delete were scoped to
    `book_id` alone, every catalogue rechunk would destroy the private chunks
    of every user who had uploaded the same book — silently, and only
    discovered when someone's library stopped answering.
    """
    with SessionLocal() as session:
        owner = session.scalar(select(User).limit(1))
        if owner is None:
            owner = User(email="chunk-owner@example.test", password_hash="x")
            session.add(owner)
            session.flush()
        owner_id = owner.id

        session.add(
            BookChunk(
                book_id=sample_book,
                user_id=owner_id,
                visibility="private",
                ordinal=0,
                content="a passage from the user's own uploaded copy",
                char_count=43,
            )
        )
        session.commit()

        chunk_one(session, sample_book, PROSE)
        session.commit()
        chunk_one(session, sample_book, PROSE)  # rechunk again for good measure
        session.commit()

        assert _count(session, sample_book, "private") == 1, "private chunk destroyed"
        assert _count(session, sample_book, "public") > 1

        private = session.scalar(
            select(BookChunk).where(
                BookChunk.book_id == sample_book, BookChunk.visibility == "private"
            )
        )
        assert private.user_id == owner_id
        assert private.content == "a passage from the user's own uploaded copy"


def test_text_with_no_usable_content_writes_nothing(sample_book):
    with SessionLocal() as session:
        assert chunk_one(session, sample_book, "   \n\n  ") == 0
        session.commit()
        assert _count(session, sample_book, "public") == 0


# ---------------------------------------------------------------------------
# Origin — section 11 auditability, and the two passes coexisting.
# ---------------------------------------------------------------------------

DESCRIPTION = (
    "A sweeping account of a coastal village and the families who fished "
    "there for four generations, told through the letters they left behind."
)


def test_chunking_a_description_does_not_delete_the_full_text_chunks(sample_book):
    """The two passes write to one table. If either delete were scoped to
    book_id and visibility alone, running one pass would wipe the other's
    work — and the symptom would be a book quietly falling out of search.
    """
    with SessionLocal() as session:
        text_chunks = chunk_one(session, sample_book, PROSE, origin="text")
        session.commit()
        desc_chunks = chunk_one(session, sample_book, DESCRIPTION, origin="description")
        session.commit()

        assert text_chunks > 1
        assert desc_chunks >= 1

        rows = session.scalars(
            select(BookChunk).where(BookChunk.book_id == sample_book)
        ).all()
        origins = [r.origin for r in rows]
        assert origins.count("text") == text_chunks
        assert origins.count("description") == desc_chunks


def test_rechunking_text_leaves_the_description_alone(sample_book):
    with SessionLocal() as session:
        chunk_one(session, sample_book, DESCRIPTION, origin="description")
        session.commit()
        chunk_one(session, sample_book, PROSE, origin="text")
        session.commit()
        chunk_one(session, sample_book, PROSE, origin="text")  # again
        session.commit()

        assert _count_origin(session, sample_book, "description") == 1


def test_ordinals_from_the_two_origins_do_not_collide(sample_book):
    """Both origins number from zero. Without the offset the unique index
    (book_id, user_id, ordinal) rejects the second pass outright.
    """
    with SessionLocal() as session:
        chunk_one(session, sample_book, PROSE, origin="text")
        chunk_one(session, sample_book, DESCRIPTION, origin="description")
        session.commit()

        ordinals = [
            r.ordinal
            for r in session.scalars(
                select(BookChunk).where(BookChunk.book_id == sample_book)
            )
        ]
        assert len(ordinals) == len(set(ordinals)), "ordinal collision across origins"


def _count_origin(session, book_id, origin):
    return session.scalar(
        select(func.count())
        .select_from(BookChunk)
        .where(BookChunk.book_id == book_id, BookChunk.origin == origin)
    )
