"""`/pages` for an upload — OI-4's ingest-only line, F-68 closed by-design.

OI-4, extended 2026-09-22 for Phase 4, draws the line precisely: *extraction
and retrieval are permitted, rendering is not.* A reader may upload a book
they own; the app extracts, chunks, embeds and searches it, and answers
grounded in it. It does not render its pages back.

F-68 was filed as a bug — "an uploaded book cannot be read, only summarised"
— and is not one. The behaviour was right and the *message* was wrong: the
owner got `404 Book not found` for a book that was demonstrably present and
answering questions, which reads as the app having lost it rather than as a
policy.

So the only change is what the owner is told, and the only thing these tests
really guard is that telling them did not open a hole. A non-owner must get
an answer indistinguishable from a book that does not exist — not a 403, not
a different message. A 403 would confirm the book exists, and for a private
upload that is the thing being protected.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

try:
    from conftest import database_reachable

    HAVE_DB = database_reachable()
except Exception:  # pragma: no cover
    HAVE_DB = False

pytestmark = pytest.mark.skipif(not HAVE_DB, reason="database unavailable")

TEXT = ("CHAPTER ONE\n\nThe tide went out and did not come back. " * 70).encode()


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as c:
        yield c


def _register(client):
    email = f"pages_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register",
                    json={"email": email, "password": "Str0ng!Passw0rd1"})
    assert r.status_code < 400, r.text
    body = r.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


@pytest.fixture(scope="module")
def upload(client):
    """One ingested upload and its owner's headers."""
    headers, user_id = _register(client)
    r = client.post(
        "/api/library/upload",
        headers=headers,
        files={"file": ("The Tide.txt", TEXT, "text/plain")},
        data={"attests_ownership": "true"},
    )
    if r.status_code == 429:
        pytest.skip("upload rate limit reached")
    assert r.status_code == 200, r.text
    body = r.json()

    if body.get("job_id"):
        for _ in range(40):
            state = client.get(f"/api/library/jobs/{body['job_id']}").json()
            if state.get("status") in ("done", "succeeded", "failed", "error"):
                break
            time.sleep(0.5)

    yield {"headers": headers, "user_id": user_id, "book_id": body["book_id"]}

    # Cascading user delete, the F-59/F-61 pattern the other upload tests use.
    from sqlalchemy import select

    from db import SessionLocal
    from models import Book, User

    import main as main_mod

    with SessionLocal() as session:
        for stored in session.scalars(
            select(Book.storage_path).where(Book.owner_id == user_id)
        ).all():
            if stored:
                (main_mod.UPLOADS_DIR / stored).unlink(missing_ok=True)
        user = session.get(User, user_id)
        if user is not None:
            session.delete(user)
            session.commit()


# -- the part that is deliberately not built ------------------------------


def test_the_upload_is_ingested_and_answers_questions(client, upload):
    """The premise. Without this the tests below could pass against a book
    that simply failed to ingest, which would prove nothing about the
    rendering line."""
    r = client.post(
        f"/api/books/{upload['book_id']}/ask",
        headers=upload["headers"],
        json={"question": "What happened to the tide?"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["citations"], "the upload was not retrievable"


def test_the_owner_is_told_why_rather_than_that_it_is_missing(client, upload):
    """F-68's actual fix. Not 404: the reader's own book is there and
    answering, so "not found" describes a fault that did not happen."""
    r = client.get(f"/api/books/{upload['book_id']}/pages",
                   headers=upload["headers"])
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["text_available"] is False
    assert body["items"] == [] and body["total"] == 0
    # The reason has to name the policy, not restate the symptom.
    reason = body["reason"].lower()
    assert "your own upload" in reason
    assert "does not display" in reason
    # And it must not read as a promise that pages are coming.
    assert "yet" not in reason


def test_no_page_of_an_upload_is_ever_served(client, upload):
    """The line OI-4 draws, checked past page 1 and past the end. Rendering
    is not permitted at any offset."""
    for page in (1, 2, 50):
        r = client.get(
            f"/api/books/{upload['book_id']}/pages",
            headers=upload["headers"],
            params={"page": page, "page_size": 24},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == [], f"page {page} returned content"
        assert body["text_available"] is False


# -- the part that must not have been opened ------------------------------


def test_another_reader_cannot_tell_the_upload_exists(client, upload):
    """The whole risk of the F-68 change. A 403, or any message that differs
    from a nonexistent id, confirms a private book exists."""
    other_headers, _ = _register(client)

    mine = client.get(f"/api/books/{upload['book_id']}/pages", headers=other_headers)
    # An id far past the catalogue and past any upload.
    nonexistent = client.get("/api/books/99999999/pages", headers=other_headers)

    assert mine.status_code == 404
    assert mine.status_code == nonexistent.status_code
    assert mine.json() == nonexistent.json(), (
        "a non-owner can distinguish a private upload from a missing book"
    )


def test_an_anonymous_caller_cannot_tell_either(client, upload):
    """`/pages` takes `OptionalUser` — reading has never required an account
    (§12) — so the unauthenticated path needs the same guarantee."""
    mine = client.get(f"/api/books/{upload['book_id']}/pages")
    nonexistent = client.get("/api/books/99999999/pages")

    assert mine.status_code == 404
    assert mine.json() == nonexistent.json()


def test_the_catalogue_path_is_unchanged(client):
    """A Gutenberg book still reads. The F-68 change must not have moved the
    one case OI-4 does permit."""
    listed = client.get("/books?limit=1&audiobook=true").json()["items"][0]
    r = client.get(f"/api/books/{listed['id']}/pages")

    assert r.status_code == 200
    body = r.json()
    # Either real text or the honest empty state — but reached through the
    # catalogue branch, so never the upload reason.
    assert "your own upload" not in body.get("reason", "").lower()
