"""services/library_ingest.process_upload and chunk_one's user_id extension
— section 29's Extract -> Chunk -> Embed, exercised directly rather than
through the HTTP layer (test_upload_endpoint.py already covers that path
end to end; these focus on the pipeline's own failure handling and the
isolation guarantee that matters most: an uploaded chunk must never be
reachable by anyone but its owner, through the real retrieval path).
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)

from db import SessionLocal  # noqa: E402
from embeddings import get_backend  # noqa: E402
from models import Book, BookChunk, BookText, User  # noqa: E402
from scripts.chunk_pass import chunk_one  # noqa: E402
from services.library_ingest import process_upload  # noqa: E402
from sqlalchemy import select  # noqa: E402

LONG_TEXT = (
    "Chapter One. It was a dark and stormy night, and the wind howled "
    "through the trees outside the old manor house, rattling the windows "
    "in their frames and making the candle flames dance wildly on the "
    "dining room table where three strangers sat in uneasy silence."
)


@pytest.fixture
def private_book(tmp_path):
    """A private book row plus a real file under a temp uploads dir —
    exactly the shape `api/library.py` produces, built directly so these
    tests do not depend on the HTTP layer.
    """
    tag = uuid.uuid4().hex[:12]
    with SessionLocal() as session:
        user = User(email=f"ingest-{tag}@example.com", password_hash="x")
        session.add(user)
        session.flush()

        storage_name = f"{tag}.txt"
        (tmp_path / storage_name).write_bytes(LONG_TEXT.encode("utf-8"))

        book = Book(
            source="upload",
            external_id=tag,
            owner_id=user.id,
            title="Ingest Test Book",
            upload_status="uploaded",
            source_filename="test.txt",
            file_format="txt",
            storage_path=storage_name,
            file_size_bytes=len(LONG_TEXT),
        )
        session.add(book)
        session.commit()
        ids = (user.id, book.id)

    yield {"user_id": ids[0], "book_id": ids[1], "uploads_dir": tmp_path}

    with SessionLocal() as session:
        u = session.get(User, ids[0])
        if u is not None:
            session.delete(u)
            session.commit()


def test_process_upload_reaches_ready(private_book):
    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is True
    assert result["upload_status"] == "ready"
    assert result["chunks"] > 0

    with SessionLocal() as session:
        book = session.get(Book, private_book["book_id"])
        assert book.upload_status == "ready"

        text_row = session.get(BookText, private_book["book_id"])
        assert text_row.content == LONG_TEXT
        assert text_row.is_complete is True

        chunks = session.scalars(
            select(BookChunk).where(BookChunk.book_id == private_book["book_id"])
        ).all()
        assert len(chunks) > 0
        assert all(c.visibility == "private" for c in chunks)
        assert all(c.user_id == private_book["user_id"] for c in chunks)
        assert all(c.embedding is not None for c in chunks)


def test_process_upload_on_a_catalogue_book_is_refused(tmp_path):
    """The structural guard: this pipeline must never run over a row with
    no owner, whatever called it."""
    with SessionLocal() as session:
        catalogue_book = session.scalar(select(Book).where(Book.owner_id.is_(None)))
        assert catalogue_book is not None
        book_id = catalogue_book.id

    result = process_upload(book_id, tmp_path)
    assert result["ok"] is False
    assert "not a private upload" in result["error"]


def test_process_upload_on_a_missing_book_is_reported_not_raised(tmp_path):
    result = process_upload(999_999_999, tmp_path)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_process_upload_missing_file_marks_extraction_failed(private_book):
    with SessionLocal() as session:
        book = session.get(Book, private_book["book_id"])
        book.storage_path = "this-file-does-not-exist.txt"
        session.commit()

    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is False
    assert "extraction failed" in result["error"]

    with SessionLocal() as session:
        book = session.get(Book, private_book["book_id"])
        assert book.upload_status == "extraction_failed"


def test_process_upload_empty_extraction_is_reported_not_ready(private_book):
    with SessionLocal() as session:
        storage_path = session.get(Book, private_book["book_id"]).storage_path
    (private_book["uploads_dir"] / storage_path).write_bytes(b"   \n\n  ")

    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is False
    assert "empty" in result["error"]
    with SessionLocal() as session:
        book = session.get(Book, private_book["book_id"])
        assert book.upload_status == "extraction_failed"


def test_process_upload_is_idempotent_on_rerun(private_book):
    """Re-running ingest (a retry, or a reprocess trigger) must replace
    the previous chunks, not pile up duplicates alongside them."""
    first = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert first["ok"] is True
    second = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert second["ok"] is True
    assert second["chunks"] == first["chunks"]

    with SessionLocal() as session:
        chunks = session.scalars(
            select(BookChunk).where(BookChunk.book_id == private_book["book_id"])
        ).all()
        assert len(chunks) == first["chunks"], "rerun must not duplicate chunks"


def test_embedding_failure_is_reported_as_embedding_failed(private_book, monkeypatch):
    import services.library_ingest as li

    monkeypatch.setattr(li, "embed_run", lambda **kw: {"embedded": 0, "failed": 1})
    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is False
    assert "embedding" in result["error"].lower()
    with SessionLocal() as session:
        book = session.get(Book, private_book["book_id"])
        assert book.upload_status == "embedding_failed"


# ---------------------------------------------------------------------------
# chunk_one's user_id extension, and the isolation that depends on it
# ---------------------------------------------------------------------------


def test_chunk_one_default_behaviour_is_unchanged():
    """user_id=None (every pre-existing caller) must still write public,
    ownerless chunks — the exact contract test_chunk_pass.py already
    covers, re-asserted here at the call site that matters for this slice.
    """
    with SessionLocal() as session:
        book = session.scalar(select(Book).where(Book.owner_id.is_(None)))
        book_id = book.id
        original = list(
            session.scalars(select(BookChunk).where(BookChunk.book_id == book_id))
        )
        snapshot = [
            {
                "user_id": r.user_id, "visibility": r.visibility, "ordinal": r.ordinal,
                "origin": r.origin, "content": r.content, "char_count": r.char_count,
                "embedding": r.embedding, "embedding_model": r.embedding_model,
            }
            for r in original
        ]
        for r in original:
            session.expunge(r)
        try:
            n = chunk_one(session, book_id, LONG_TEXT, origin="text")
            session.commit()
            assert n > 0
            rows = session.scalars(
                select(BookChunk).where(BookChunk.book_id == book_id)
            ).all()
            assert all(r.visibility == "public" for r in rows)
            assert all(r.user_id is None for r in rows)
        finally:
            session.execute(
                BookChunk.__table__.delete().where(
                    BookChunk.book_id == book_id, BookChunk.visibility == "public",
                    BookChunk.origin == "text",
                )
            )
            session.add_all(BookChunk(book_id=book_id, **f) for f in snapshot)
            session.commit()


def test_chunk_one_private_rechunk_never_touches_public_chunks_of_the_same_book(private_book):
    """The property that matters most: rechunking one owner's private copy
    of a book must not delete (or leak into) that book_id's public rows —
    the exact regression `test_rechunking_the_catalogue_never_touches_private_chunks`
    already guards the other direction for."""
    with SessionLocal() as session:
        session.add(BookChunk(
            book_id=private_book["book_id"], user_id=None, visibility="public",
            origin="text", ordinal=0, content="a public chunk that must survive",
            char_count=33,
        ))
        session.commit()

        chunk_one(session, private_book["book_id"], LONG_TEXT, origin="text", user_id=private_book["user_id"])
        session.commit()

        public_rows = session.scalars(
            select(BookChunk).where(
                BookChunk.book_id == private_book["book_id"], BookChunk.visibility == "public"
            )
        ).all()
        assert len(public_rows) == 1
        assert public_rows[0].content == "a public chunk that must survive"

        private_rows = session.scalars(
            select(BookChunk).where(
                BookChunk.book_id == private_book["book_id"], BookChunk.visibility == "private"
            )
        ).all()
        assert len(private_rows) > 0
        assert all(r.user_id == private_book["user_id"] for r in private_rows)


def test_librarians_real_search_private_closure_finds_the_upload(private_book, fitted_app):
    """`services.librarian.default_deps()`'s real `search_private` (not the
    fake `test_librarian.py` exercises for the tool-loop logic) is thin
    wiring around `search_chunks` — this proves that wiring is actually
    correct against a real ingested upload, closing the one seam the fake
    deliberately does not cover.

    `fitted_app` is required only to have `main` (and everything it imports,
    including `api.search`) fully booted before `default_deps()`'s lazy
    `from api.search import ...` runs — calling it cold, first, before
    anything else in the process has imported `main`, hits an import-order
    circularity that is specific to this test's isolation, not a real one
    (the app itself boots this exact code cleanly; verified separately).
    """
    from services.librarian import default_deps

    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is True

    deps = default_deps()
    hits = deps.search_private(
        private_book["user_id"], "dark and stormy night manor house", 40
    )
    assert any(h["book_id"] == private_book["book_id"] for h in hits)
    assert all("book_id" in h and "title" in h and "passage" in h for h in hits)

    # And the isolation guarantee holds through this exact entry point too.
    other_hits = deps.search_private(
        private_book["user_id"] + 1_000_000, "dark and stormy night", 40
    )
    assert not any(h["book_id"] == private_book["book_id"] for h in other_hits)


def test_an_uploaded_chunk_is_unreachable_through_the_real_search_path(private_book):
    """Not just a visibility flag checked in isolation — the actual
    retrieval function anonymous and other-user searches go through.
    """
    from services.search import search_chunks

    result = process_upload(private_book["book_id"], private_book["uploads_dir"])
    assert result["ok"] is True

    encoder = get_backend("minilm")
    query_vec = encoder.encode(["dark and stormy night manor house candle"])[0].tolist()

    with SessionLocal() as session:
        anonymous_hits = search_chunks(
            session, query_vec, user_id=None, limit=50, min_similarity=0.0
        )
        assert private_book["book_id"] not in {h.book_id for h in anonymous_hits}

        other_user_hits = search_chunks(
            session, query_vec, user_id=private_book["user_id"] + 1_000_000,
            limit=50, min_similarity=0.0,
        )
        assert private_book["book_id"] not in {h.book_id for h in other_user_hits}

        owner_hits = search_chunks(
            session, query_vec, user_id=private_book["user_id"], limit=50, min_similarity=0.0
        )
        assert private_book["book_id"] in {h.book_id for h in owner_hits}, (
            "the owner's own upload must be findable by the owner"
        )
