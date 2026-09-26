"""POST/GET /api/library/* — sections 29/30, Phase 4 slice 2a.

Endpoint tests for the upload handler itself: authentication, attestation,
format validation (including the deliberate PDF deferral), size limits, and
the F-04/F-05-shaped question of what a caller-supplied filename can and
cannot influence. `test_upload_isolation.py` already covers what happens to
an uploaded row once it exists (enrichment/ingest/vector-pass isolation);
these tests cover how that row comes to exist in the first place.
"""

from __future__ import annotations

import io
import sys
import uuid
import zipfile
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
from models import Book, catalogue_only  # noqa: E402
from sqlalchemy import select  # noqa: E402

import ratelimit  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_ratelimit():
    """Every test in this file uploads through the same TestClient, so
    without this the IP bucket accumulates across tests regardless of which
    (fresh, per-test) account is uploading — see test_ratelimit.py for the
    same pattern."""
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(scope="module")
def client(fitted_app):
    _, c = fitted_app
    return c


def _epub_bytes(mimetype: bytes = b"application/epub+zip") -> bytes:
    """The minimum a real EPUB reader needs: a zip carrying a `mimetype`
    entry naming itself. Not spec-conformant (mimetype should be first and
    stored, not deflated) — this only needs to pass `_validate_content`,
    which is deliberately not a full EPUB parser (that is extraction's job,
    a later slice)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", mimetype)
        zf.writestr("content.opf", "<package/>")
    return buf.getvalue()


def _real_epub_bytes() -> bytes:
    """A structurally real EPUB (proper OPF/spine/nav, via `ebooklib`
    itself), for testing extraction rather than just the upload-time
    content sniff `_epub_bytes()` above exists for.

    Two chapters with clearly distinguishable content, plus a real
    navigation document — the nav's own text (a chapter-title list) must
    not leak into the extracted text; `EpubHtml.is_chapter()` is what
    `services/extraction.py` relies on to exclude it.
    """
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(uuid.uuid4().hex)
    book.set_title("A Tale")
    book.set_language("en")
    book.add_author("Test Author")

    c1 = epub.EpubHtml(title="Chapter 1", file_name="chap1.xhtml", lang="en")
    c1.content = (
        "<html><body><h1>Chapter 1</h1>"
        "<p>It was the best of times, it was the worst of times, "
        "it was the age of wisdom, it was the age of foolishness.</p>"
        "</body></html>"
    )
    c2 = epub.EpubHtml(title="Chapter 2", file_name="chap2.xhtml", lang="en")
    c2.content = (
        "<html><body><h1>Chapter 2</h1>"
        "<p>London and Paris were, in this respect, so far removed from "
        "their high places that things in general were settled for ever.</p>"
        "</body></html>"
    )
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (c1, c2)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", c1, c2]

    buf = io.BytesIO()
    epub.write_epub(buf, book)
    return buf.getvalue()


@pytest.fixture
def uploader(client):
    """A fresh registered, logged-in reader. Cleans up its own uploads —
    both the DB rows (via the cascading user delete, F-59/F-61's pattern)
    and the files they point at, since deleting a Book row does not touch
    whatever `main.UPLOADS_DIR` holds for it.
    """
    import main

    tag = uuid.uuid4().hex[:12]
    email = f"uploader-{tag}@example.com"
    r = client.post(
        "/api/auth/register", json={"email": email, "password": "correct horse battery"}
    )
    assert r.status_code < 400, r.text
    token = r.json()["access_token"]
    user_id = r.json()["user"]["id"]

    yield {"client": client, "token": token, "user_id": user_id}

    with SessionLocal() as session:
        from models import User

        paths = session.scalars(
            select(Book.storage_path).where(Book.owner_id == user_id)
        ).all()
        for p in paths:
            if p:
                (main.UPLOADS_DIR / p).unlink(missing_ok=True)
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)  # books.owner_id cascades
            session.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def wait_for_job(client, job_id: str, timeout: float = 30.0) -> dict:
    """Poll an ingest job to a terminal state.

    Upload now triggers Extract -> Chunk -> Embed in a background thread
    (`jobs.LIBRARY_REGISTRY`) rather than finishing synchronously. A test
    that asserts on the resulting `BookText`/`BookChunk` rows — or that
    tears down by deleting the file the job is still reading — has to wait
    for that thread first; on Windows, unlinking a file the job still has
    open raises `PermissionError`, which is exactly how this was found.
    """
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/library/jobs/{job_id}").json()
        if body.get("status") in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_upload_requires_authentication(client):
    r = client.post(
        "/api/library/upload",
        files={"file": ("book.txt", b"some pages of prose", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 401


def test_upload_without_attestation_is_rejected(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.txt", b"some pages of prose", "text/plain")},
        data={"attests_ownership": "false"},
    )
    assert r.status_code == 400
    assert "attest" in r.json()["error"]["message"].lower() or "own" in r.json()["error"]["message"].lower()

    with SessionLocal() as session:
        count = session.scalar(
            select(Book).where(Book.owner_id == uploader["user_id"])
        )
        assert count is None, "a rejected attestation must not create a book row"


TXT_BODY = (
    b"Chapter One.\n\nIt was a dark and stormy night. The wind howled through "
    b"the old house, rattling every window and door, and the candle on the "
    b"table guttered but did not go out. Somewhere upstairs, a floorboard "
    b"creaked, though no one else was supposed to be home."
)


def test_upload_txt_succeeds(uploader):
    content = TXT_BODY
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("My Book.txt", content, "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["file_format"] == "txt"
    assert body["upload_status"] == "uploaded"
    assert body["attestation_version"]
    assert body["source_filename"] == "My Book.txt"
    assert body["file_size_bytes"] == len(content)
    assert body["attested_at"] is not None
    assert body["job_id"]
    assert body["poll"] == f"/api/library/jobs/{body['job_id']}"

    with SessionLocal() as session:
        book = session.get(Book, body["book_id"])
        assert book is not None
        assert book.owner_id == uploader["user_id"]
        assert book.source == "upload"
        assert book.file_format == "txt"

        import main
        stored = main.UPLOADS_DIR / book.storage_path
        assert stored.exists()
        assert stored.read_bytes() == content

    # Section 29's Extract -> Chunk -> Embed, run in the background.
    job = wait_for_job(uploader["client"], body["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["ok"] is True
    assert job["result"]["chunks"] > 0

    with SessionLocal() as session:
        from models import BookChunk, BookText

        book = session.get(Book, body["book_id"])
        assert book.upload_status == "ready"

        text_row = session.get(BookText, book.id)
        assert text_row is not None
        assert text_row.content == content.decode("utf-8")
        assert text_row.is_complete is True, "the whole upload was extracted, not an excerpt"
        assert text_row.source == "txt"

        chunks = session.scalars(
            select(BookChunk).where(BookChunk.book_id == book.id)
        ).all()
        assert len(chunks) > 0
        assert all(c.visibility == "private" for c in chunks)
        assert all(c.user_id == uploader["user_id"] for c in chunks)
        assert all(c.origin == "text" for c in chunks)
        assert all(c.embedding is not None for c in chunks)
        assert all(c.embedding_model == "minilm" for c in chunks)


def test_upload_epub_succeeds(uploader):
    content = _real_epub_bytes()
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("novel.epub", content, "application/epub+zip")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_format"] == "epub"

    job = wait_for_job(uploader["client"], body["job_id"])
    assert job["status"] == "done", job

    with SessionLocal() as session:
        from models import BookChunk, BookText

        book = session.get(Book, body["book_id"])
        assert book.upload_status == "ready"

        text_row = session.get(BookText, book.id)
        assert text_row is not None
        # The nav document's own generated text (title + a chapter-name
        # list) must not appear — only the real chapters. If it leaked in,
        # "Chapter 1" would appear twice (the nav's list entry, plus the
        # real <h1> heading) instead of once.
        assert "It was the best of times" in text_row.content
        assert "London and Paris" in text_row.content
        assert text_row.content.count("Chapter 1") == 1
        assert "A Tale" not in text_row.content, "the book title is the nav's, not a chapter's"

        chunks = session.scalars(
            select(BookChunk).where(BookChunk.book_id == book.id)
        ).all()
        assert len(chunks) > 0
        assert all(c.visibility == "private" for c in chunks)


def test_upload_rejects_pdf_as_deferred(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.pdf", b"%PDF-1.4 fake but well-formed-looking", "application/pdf")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400
    msg = r.json()["error"]["message"].lower()
    # Specifically the deferred-format message, not just "unsupported" —
    # "pdf" alone would also appear in the generic rejection's echoed
    # extension (".pdf"), which would let this test pass even if the
    # dedicated DEFERRED_FORMATS branch were deleted entirely.
    assert "not built yet" in msg or "extraction" in msg
    assert "unsupported" not in msg
    with SessionLocal() as session:
        assert session.scalar(select(Book).where(Book.owner_id == uploader["user_id"])) is None


def test_upload_rejects_unsupported_extension(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.mobi", b"whatever", "application/octet-stream")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400
    assert "unsupported" in r.json()["error"]["message"].lower()


def test_upload_rejects_binary_content_labelled_txt(uploader):
    """The extension says text; the bytes say otherwise."""
    garbage = bytes(range(256)) * 4
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.txt", garbage, "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400


def test_upload_rejects_non_zip_labelled_epub(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.epub", b"this is not a zip archive at all", "application/epub+zip")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400
    assert "epub" in r.json()["error"]["message"].lower()


def test_upload_rejects_zip_with_wrong_mimetype_as_epub(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.epub", _epub_bytes(mimetype=b"application/zip"), "application/epub+zip")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400


def test_upload_rejects_empty_file(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.txt", b"", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 400
    assert "empty" in r.json()["error"]["message"].lower()


def test_upload_rejects_oversized_file(uploader, monkeypatch):
    import api.library as library

    monkeypatch.setattr(library, "MAX_UPLOAD_BYTES", 100)
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("book.txt", b"x" * 200, "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 413


def test_uploaded_filename_never_becomes_a_filesystem_path(uploader):
    """F-04/F-05's exact shape: a caller-supplied string must never become
    part of a path. `source_filename` is stored verbatim for display;
    `storage_path` must never contain any trace of it.
    """
    import main

    malicious = "../../../../etc/passwd.txt"
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": (malicious, b"harmless content", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_filename"] == malicious, "shown verbatim for display"
    wait_for_job(uploader["client"], body["job_id"])

    with SessionLocal() as session:
        book = session.get(Book, body["book_id"])
        # The actual contract, not just "no traversal": a name built from
        # the caller's basename ("passwd.txt") would pass a no-".." check
        # while still putting caller input on disk.
        assert book.storage_path == f"{book.external_id}.txt"
        assert "passwd" not in book.storage_path
        resolved = (main.UPLOADS_DIR / book.storage_path).resolve()
        assert resolved.parent == main.UPLOADS_DIR.resolve(), (
            "the stored file must live inside UPLOADS_DIR, not wherever the "
            "caller's filename would have pointed"
        )
        assert resolved.read_bytes() == b"harmless content"

    # And nothing was written at the path a naive join would have produced.
    naive = (main.UPLOADS_DIR / malicious).resolve()
    assert not naive.exists() or naive == (main.UPLOADS_DIR / "passwd.txt").resolve()


def test_uploaded_book_is_excluded_from_catalogue_queries(uploader):
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("mine.txt", b"a private manuscript", "text/plain")},
        data={"attests_ownership": "true"},
    )
    body = r.json()
    book_id = body["book_id"]
    wait_for_job(uploader["client"], body["job_id"])

    with SessionLocal() as session:
        ids = set(session.scalars(catalogue_only(select(Book.id))))
        assert book_id not in ids


def test_list_my_library_returns_only_my_own_uploads(uploader, client):
    r1 = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("mine.txt", b"only mine", "text/plain")},
        data={"attests_ownership": "true"},
    )
    my_body = r1.json()
    my_book_id = my_body["book_id"]
    wait_for_job(uploader["client"], my_body["job_id"])

    # A second, unrelated reader uploads their own book.
    tag = uuid.uuid4().hex[:12]
    r2 = client.post(
        "/api/auth/register",
        json={"email": f"other-{tag}@example.com", "password": "correct horse battery"},
    )
    other_token = r2.json()["access_token"]
    other_user_id = r2.json()["user"]["id"]
    try:
        other_upload = client.post(
            "/api/library/upload",
            headers=_auth(other_token),
            files={"file": ("theirs.txt", b"not yours", "text/plain")},
            data={"attests_ownership": "true"},
        )
        wait_for_job(client, other_upload.json()["job_id"])

        listing = uploader["client"].get("/api/library", headers=_auth(uploader["token"]))
        assert listing.status_code == 200
        ids = [b["book_id"] for b in listing.json()["books"]]
        assert my_book_id in ids
        with SessionLocal() as session:
            other_books = session.scalars(
                select(Book.id).where(Book.owner_id == other_user_id)
            ).all()
        assert not (set(other_books) & set(ids)), "another reader's upload leaked into this listing"
    finally:
        import main
        with SessionLocal() as session:
            from models import User

            paths = session.scalars(
                select(Book.storage_path).where(Book.owner_id == other_user_id)
            ).all()
            for p in paths:
                if p:
                    (main.UPLOADS_DIR / p).unlink(missing_ok=True)
            user = session.get(User, other_user_id)
            if user is not None:
                session.delete(user)
                session.commit()


def test_upload_is_rate_limited(uploader, monkeypatch):
    monkeypatch.setattr(ratelimit, "UPLOAD_LIMIT", 1)
    monkeypatch.setattr(ratelimit, "UPLOAD_WINDOW", 60)

    first = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("one.txt", b"first", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert first.status_code == 200
    wait_for_job(uploader["client"], first.json()["job_id"])

    second = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("two.txt", b"second", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert second.status_code == 429
