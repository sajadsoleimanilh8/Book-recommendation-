"""HTTP surface of semantic search — section 27.

`test_search.py` proves the retrieval layer enforces visibility. These tests
prove the endpoint actually reaches that layer with the right caller identity,
which is a separate failure: a correct filter is no use if the route passes
`user_id=None` for a signed-in user, or worse, someone else's id.
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

ENCODER = get_backend("hashing")
PUBLIC_TEXT = "a public passage about whales harpoons and the wide grey ocean"
SECRET_TEXT = "carol private manuscript mentioning zarquon the unmistakable token"


@pytest.fixture
def client(fitted_app, monkeypatch):
    """The real app, with the deterministic encoder swapped in.

    The endpoint defaults to the LSA backend, whose vectors mean nothing
    against the fixture chunks below. Pinning both sides to `hashing` keeps
    these tests about the HTTP and authorization layers rather than about
    whether a model happens to be fitted on this machine.
    """
    main_module, test_client = fitted_app
    monkeypatch.setattr(main_module, "_SEARCH_ENCODER", ENCODER, raising=False)
    monkeypatch.setattr(main_module, "_SEARCH_ENCODER_ERROR", None, raising=False)
    return test_client


@pytest.fixture
def seeded(client):
    """One public chunk and one private chunk belonging to a registered user."""
    response = client.post(
        "/api/auth/register",
        json={"email": "carol.search@example.com", "password": "correct horse battery"},
    )
    if response.status_code >= 400:
        response = client.post(
            "/api/auth/login",
            json={"email": "carol.search@example.com", "password": "correct horse battery"},
        )
    assert response.status_code < 400, response.text
    token = response.json()["access_token"]

    with SessionLocal() as session:
        book_id = session.scalar(select(Book.id).limit(1))
        owner_id = session.scalar(
            select(User.id).where(User.email == "carol.search@example.com")
        )
        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))
        session.add_all(
            [
                BookChunk(
                    book_id=book_id, user_id=None, visibility="public", ordinal=0,
                    content=PUBLIC_TEXT, char_count=len(PUBLIC_TEXT),
                    embedding=ENCODER.encode([PUBLIC_TEXT])[0].tolist(),
                    embedding_model="hashing",
                ),
                BookChunk(
                    book_id=book_id, user_id=owner_id, visibility="private", ordinal=1,
                    content=SECRET_TEXT, char_count=len(SECRET_TEXT),
                    embedding=ENCODER.encode([SECRET_TEXT])[0].tolist(),
                    embedding_model="hashing",
                ),
            ]
        )
        session.commit()

    yield {"token": token, "book_id": book_id}

    with SessionLocal() as session:
        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))
        session.commit()


def _passages(payload):
    return " ".join(item.get("passage", "") for item in payload["items"])


def test_endpoint_answers_and_names_its_backend(client, seeded):
    response = client.get("/api/search/semantic", params={"q": "whales and the ocean"})
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        pytest.skip("no fitted model on this instance")

    body = response.json()
    assert body["query"] == "whales and the ocean"
    assert "backend" in body, "callers cannot compare scores without knowing the space"
    assert body["total"] == len(body["items"])


def test_anonymous_caller_cannot_reach_a_private_passage(client, seeded):
    """Querying the private text verbatim. If the route leaks, it leaks here."""
    response = client.get(
        "/api/search/semantic", params={"q": SECRET_TEXT, "by_passage": True, "limit": 50}
    )
    if response.status_code == 503:
        pytest.skip("no fitted model on this instance")
    assert response.status_code == 200
    assert "zarquon" not in _passages(response.json())


def test_owner_reaches_their_own_private_passage(client, seeded):
    """The other half. Without this, a route that always passes user_id=None
    would pass the leak test above while making private chunks unreachable
    to everyone, including their owner."""
    response = client.get(
        "/api/search/semantic",
        params={"q": SECRET_TEXT, "by_passage": True, "limit": 50},
        headers={"Authorization": f"Bearer {seeded['token']}"},
    )
    if response.status_code == 503:
        pytest.skip("no fitted model on this instance")
    assert response.status_code == 200
    assert "zarquon" in _passages(response.json())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F-34: get_current_user_optional treats an invalid token exactly like "
        "no token, so a forged or expired token silently downgrades to an "
        "anonymous search instead of 401. Shared by every OptionalUser route, "
        "so fixing it is a deliberate change, not a patch here."
    ),
)
def test_an_invalid_token_is_rejected_not_silently_downgraded(client, seeded):
    """A forged token must not quietly become an anonymous search — that
    turns an authentication failure into a successful-looking response."""
    response = client.get(
        "/api/search/semantic",
        params={"q": "whales"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_query_bounds_are_enforced(client):
    assert client.get("/api/search/semantic", params={"q": "x"}).status_code == 422
    assert client.get("/api/search/semantic", params={"q": "ok", "limit": 999}).status_code == 422
    assert client.get("/api/search/semantic").status_code == 422


def test_results_never_include_a_book_that_does_not_exist(client, seeded):
    """Section 27: the system must not invent books."""
    response = client.get("/api/search/semantic", params={"q": "whales and the ocean"})
    if response.status_code == 503:
        pytest.skip("no fitted model on this instance")

    with SessionLocal() as session:
        for item in response.json()["items"]:
            assert session.get(Book, item["book_id"]) is not None
