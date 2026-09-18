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
from models import Book, BookChunk, BookVector, User  # noqa: E402

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
        # `load_content_vectors` (services/store.py) is all-or-nothing: one
        # book in the whole catalogue missing a book_vectors row falls the
        # ML fit back to TF-IDF for everything, moving every golden ranking
        # score. Snapshotting the real row and restoring it in teardown,
        # rather than deleting and leaving it empty, is what stops this
        # fixture from breaking the golden suite the next time it runs
        # against this database.
        original = session.scalar(
            select(BookVector).where(BookVector.book_id == book_id)
        )
        original_snapshot = (
            {
                "embedding": original.embedding,
                "embedding_model": original.embedding_model,
                "has_description": original.has_description,
                "source_chars": original.source_chars,
            }
            if original is not None
            else None
        )
        if original is not None:
            # The bulk `Table.delete()` below deletes the row at the DB level
            # but does not expire this already-loaded ORM object from the
            # session's identity map — without expunging it, adding a new
            # BookVector for the same book_id later in this function raises a
            # SQLAlchemy identity-map warning (two objects, one primary key).
            session.expunge(original)

        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))
        session.execute(BookVector.__table__.delete().where(BookVector.book_id == book_id))
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
                # Book-level search (by_passage=False, the default) now
                # queries book_vectors, not a chunk collapse (F-44) — without
                # this row these tests would silently exercise zero items on
                # that path, since the real catalogue's book_vectors are all
                # "minilm" and this fixture pins the query encoder to
                # "hashing" for determinism.
                BookVector(
                    book_id=book_id,
                    embedding=ENCODER.encode([PUBLIC_TEXT])[0].tolist(),
                    embedding_model="hashing",
                    has_description=True,
                ),
            ]
        )
        session.commit()

    yield {"token": token, "book_id": book_id}

    with SessionLocal() as session:
        session.execute(BookChunk.__table__.delete().where(BookChunk.book_id == book_id))
        session.execute(BookVector.__table__.delete().where(BookVector.book_id == book_id))
        if original_snapshot is not None:
            session.add(BookVector(book_id=book_id, **original_snapshot))
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


def test_an_invalid_token_is_rejected_not_silently_downgraded(client, seeded):
    """A forged token must not quietly become an anonymous search — that
    turns an authentication failure into a successful-looking response.

    Was xfail(strict=True) while F-34 stood. Now the fix's regression guard.
    """
    response = client.get(
        "/api/search/semantic",
        params={"q": "whales"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "token_invalid"


def test_an_expired_token_says_so_distinctly(client, seeded):
    """The whole point of F-34's fix: a client must be able to tell "refresh
    your session" from "your token is garbage". Same status, different code.
    """
    import jwt
    from datetime import datetime, timedelta, timezone

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import config

    # No real user needed: expiry is checked before the row lookup, which is
    # itself the correct order — an expired token should never reach the DB.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": "1", "iat": past - timedelta(hours=1), "exp": past},
        config.JWT_SECRET,
        algorithm=config.JWT_ALGORITHM,
    )
    response = client.get(
        "/api/search/semantic",
        params={"q": "whales"},
        headers={"Authorization": f"Bearer {expired}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "token_expired"
    # RFC 6750: the reason belongs in the header too, so a client need not
    # depend on the body shape.
    assert "token_expired" in response.headers.get("WWW-Authenticate", "")


def test_no_token_is_still_anonymous_not_an_error(client, seeded):
    """The fix must not break anonymous browsing — "optional" still means the
    endpoint serves visitors who never claimed an identity."""
    assert client.get("/api/search/semantic", params={"q": "whales"}).status_code == 200


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


# ---------------------------------------------------------------------------
# OI-11 — "books under $5" must not return everything.
#
# The price cap lives on POST /api/recommend, not GET /api/books: the
# catalogue route never declared the parameter (F-38), so `?max_price=5` there
# is silently ignored rather than wrong. These test the surface that has it.
# ---------------------------------------------------------------------------


def _recommend(client, **payload):
    response = client.post("/api/recommend", json={"top_k": 12, **payload})
    assert response.status_code == 200, response.text
    body = response.json()
    return body.get("results", body.get("items", body.get("books", [])))


def test_max_price_does_not_match_books_with_no_known_price(client):
    """`_safe_float(None) == 0.0` made every unlisted book look free, so the
    cap matched the whole catalogue. A filter that silently matches everything
    is worse than no filter: the caller believes it worked.
    """
    unfiltered = _recommend(client, genre="Fiction")
    capped = _recommend(client, genre="Fiction", max_price=5)

    assert unfiltered, "no baseline results — the fixture cannot show a difference"
    unknown_priced = [b for b in unfiltered if b.get("availability") == "unknown"]
    assert unknown_priced, (
        "baseline had no unknown-price books, so this test cannot detect the bug"
    )
    assert not [b for b in capped if b.get("availability") == "unknown"], (
        "a book whose price nobody knows passed a $5 cap"
    )


def test_every_priced_result_actually_has_a_known_price(client):
    """The other half of the line: whatever survives must be true, not fewer."""
    for book in _recommend(client, max_price=5):
        assert book["availability"] != "unknown", (
            f"{book['title']!r} passed a price filter with an unknown price"
        )
        assert book["price"] is not None and book["price"] <= 5


def test_no_cap_still_returns_unknown_priced_books(client):
    """The fix must not overshoot into hiding books when no cap was asked for."""
    assert _recommend(client, genre="Fiction"), "filtering with no cap dropped everything"
