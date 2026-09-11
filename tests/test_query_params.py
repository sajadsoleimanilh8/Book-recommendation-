"""F-38 — an undeclared query parameter is a 422, not a shrug.

`GET /api/books?max_price=5` returned all 29,975 books with HTTP 200. Not
because the filter was broken: the route never declared `max_price`, and
FastAPI discards parameters it does not know about. The caller believes they
filtered, the server says 200, and the data disagrees.

Every typo behaves identically — `ratingmin`, `limt`, `max_pages`. This is the
same failure shape as OI-11 one layer up, and it gets more expensive with
every client added.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.fixture(scope="module")
def client(fitted_app):
    _, c = fitted_app
    return c


def test_the_parameter_that_found_this(client):
    """/api/books never declared max_price, so the cap was silently dropped."""
    response = client.get("/api/books", params={"limit": 1, "max_price": 5})

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "unknown_query_parameter"
    assert body["unknown"] == ["max_price"]


def test_a_typo_is_rejected_rather_than_ignored(client):
    """`limt=2` used to return the default page size with a 200."""
    response = client.get("/api/books", params={"limt": 2})
    assert response.status_code == 422
    assert response.json()["error"]["unknown"] == ["limt"]


def test_the_error_says_what_would_have_worked(client):
    """A rejection that does not name the alternatives just moves the guessing."""
    accepted = client.get("/api/books", params={"limt": 2}).json()["error"]["accepted"]
    assert "limit" in accepted


def test_every_unknown_parameter_is_listed_not_just_the_first(client):
    response = client.get("/api/books", params={"limt": 2, "genr": "x"})
    assert sorted(response.json()["error"]["unknown"]) == ["genr", "limt"]


def test_declared_parameters_still_work(client):
    """The obvious way to break this is to reject everything."""
    assert client.get("/api/books", params={"limit": 2}).status_code == 200
    assert client.get("/api/books", params={"limit": 2, "genre": "Fiction"}).status_code == 200


def test_no_query_string_is_untouched(client):
    assert client.get("/health").status_code == 200


def test_cache_busting_parameters_are_tolerated(client):
    """`_=1699999` is added by HTTP clients, not written by the caller.
    Failing a request over something the caller never typed is not honesty."""
    assert client.get("/api/books", params={"limit": 2, "_": "1699999"}).status_code == 200


def test_path_parameters_are_not_mistaken_for_query_parameters(client):
    """A route whose parameters are all in the path must not reject its own
    query-less requests, and must still reject genuine extras."""
    assert client.get("/api/books/1").status_code in (200, 404)
    assert client.get("/api/books/1", params={"nonsense": "1"}).status_code == 422
