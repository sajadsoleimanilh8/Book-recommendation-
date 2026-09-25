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


def test_upload_txt_succeeds(uploader):
    content = b"Chapter One.\n\nIt was a dark and stormy night."
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


def test_upload_epub_succeeds(uploader):
    content = _epub_bytes()
    r = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("novel.epub", content, "application/epub+zip")},
        data={"attests_ownership": "true"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["file_format"] == "epub"


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
    book_id = r.json()["book_id"]

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
    my_book_id = r1.json()["book_id"]

    # A second, unrelated reader uploads their own book.
    tag = uuid.uuid4().hex[:12]
    r2 = client.post(
        "/api/auth/register",
        json={"email": f"other-{tag}@example.com", "password": "correct horse battery"},
    )
    other_token = r2.json()["access_token"]
    other_user_id = r2.json()["user"]["id"]
    try:
        client.post(
            "/api/library/upload",
            headers=_auth(other_token),
            files={"file": ("theirs.txt", b"not yours", "text/plain")},
            data={"attests_ownership": "true"},
        )

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

    second = uploader["client"].post(
        "/api/library/upload",
        headers=_auth(uploader["token"]),
        files={"file": ("two.txt", b"second", "text/plain")},
        data={"attests_ownership": "true"},
    )
    assert second.status_code == 429
