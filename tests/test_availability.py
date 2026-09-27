"""Price and availability honesty — section 18, F-36.

Section 18 is unambiguous: "Do not fake price or availability. If no provider
is configured: availability = unknown, not fabricated."

Every row in the catalogue carries `list_price = 0.00` — all 29,975 of them —
and both serialisers rendered that as a price of 0, which the UI printed as
"Free". The platform was telling readers that copyrighted Goodreads and Google
Books titles cost nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GUTENBERG = {"book_id": "12345", "thumbnail": "https://www.gutenberg.org/cache/x.jpg"}
GOOGLE = {"book_id": "rOQQUJz68q8C", "thumbnail": "https://books.google.com/books?id=x"}
GOODREADS = {"book_id": "9876543", "thumbnail": "https://images.gr-assets.com/x.jpg"}


@pytest.fixture(scope="module")
def rule():
    # price_and_availability moved to services/catalogue.py in the Phase D
    # restructure — imported from its real home now, not through main.
    from services.catalogue import price_and_availability

    return price_and_availability


def test_public_domain_is_the_only_thing_called_free(rule):
    price, availability, source = rule({**GUTENBERG, "list_price": 0})
    assert source == "gutenberg"
    assert price == 0.0
    assert availability == "free_public_domain"


@pytest.mark.parametrize("row", [GOOGLE, GOODREADS], ids=["google_books", "goodreads"])
def test_a_copyrighted_title_with_no_price_data_is_unknown_not_free(rule, row):
    """The regression that matters. A placeholder 0.00 must never reach the UI
    as a price, because the UI reads 0 as "Free"."""
    price, availability, _ = rule({**row, "list_price": 0})
    assert price is None, "0.00 placeholder leaked as a real price"
    assert availability == "unknown"


def test_a_real_price_is_still_reported(rule):
    """The fix must not throw away genuine data if a provider ever supplies
    it — otherwise it trades one dishonesty for another."""
    price, availability, _ = rule({**GOODREADS, "list_price": 12.5})
    assert price == 12.5
    assert availability == "listed"


def test_both_serialisers_agree(rule):
    """They disagreed once, which is how F-36 survived: the catalogue path and
    the recommendation path each had their own copy of the rule."""
    # _row_to_book -> services/catalogue.py, _ml_to_api ->
    # services/recommendation.py in the Phase D restructure.
    from services.catalogue import _row_to_book
    from services.recommendation import _ml_to_api

    for row in (GUTENBERG, GOOGLE, GOODREADS):
        payload = {**row, "list_price": 0, "title": "T", "author": "A"}
        catalogue = _row_to_book(0, payload)
        recommended = _ml_to_api(payload, rank=1)
        assert catalogue["price"] == recommended["price"]
        assert catalogue["availability"] == recommended["availability"]


def test_availability_is_always_present_and_from_a_known_set(rule):
    """A missing field would let a frontend fall back to its old `price == 0`
    reading, which is exactly the bug."""
    allowed = {"free_public_domain", "listed", "unknown"}
    for row in (GUTENBERG, GOOGLE, GOODREADS, {}):
        _, availability, _ = rule({**row, "list_price": 0})
        assert availability in allowed


def test_the_shipped_catalogue_contains_no_real_prices():
    """Documents *why* the rule is needed, against the live database.

    If this ever fails, real price data has arrived and the "unknown" default
    should be revisited rather than left in place out of habit.
    """
    from conftest import database_reachable

    if not database_reachable():
        pytest.skip("Postgres not reachable")

    from sqlalchemy import func, select

    from db import SessionLocal
    from models import Book

    with SessionLocal() as session:
        priced = session.scalar(
            select(func.count()).select_from(Book).where(Book.list_price > 0)
        )
    assert priced == 0, (
        f"{priced} books now carry a real price — section 18's 'unknown' "
        "default should be reconsidered"
    )


# -- F-52: _gutenberg_to_api's own shape --------------------------------------
#
# Found intermittently as a bare `KeyError: 'availability'` on
# `POST /api/recommend` results: every call queries GutenbergClient.search
# unconditionally and fuses whatever it returns into the ranked results
# (services.recommendation.fuse_and_rank), so any request could surface a
# Gutenberg-sourced item. `_gutenberg_to_api` built that item's dict without
# an "availability" key at all — not even "unknown" — so a caller reading
# book["availability"] (rather than book.get(...)) crashed, but only on the
# request in which a Gutenberg result actually ranked into the top N: a
# live external search (gutendex.com) whose results vary run to run, which
# is exactly the shape of "genuinely intermittent" F-52 described.
#
# Reproduced deterministically here rather than by re-triggering the live
# search enough times to get unlucky: `_gutenberg_to_api` is a pure function
# of the dict `GutenbergClient.search` returns, and that shape is fixed and
# known without needing the network to cooperate.

GUTENBERG_SEARCH_RESULT = {
    "title": "A Free Classic", "author": "Old Author", "genre": "Fiction",
    "average_rating": None, "ratings_count": None, "download_count": 50_000,
    "page_count": None, "list_price": 0.0, "source": "gutenberg", "url": "",
    "final_score": 0.05,
}


def test_a_gutenberg_search_result_carries_an_availability_key():
    from services.recommendation import _gutenberg_to_api

    api_shape = _gutenberg_to_api(GUTENBERG_SEARCH_RESULT, rank=1)
    assert "availability" in api_shape, (
        "F-52: a caller reading book['availability'] on this shape crashes "
        "with a bare KeyError, intermittently, whenever this is the item "
        "that ranks into the fused top N"
    )
    assert api_shape["availability"] == "free_public_domain"
    assert api_shape["price"] == 0.0


def test_gutenberg_and_catalogue_results_agree_on_availability_after_fusion():
    """The actual failure mode: a caller iterating `fuse_and_rank`'s output
    and reading `book["availability"]` unconditionally, the way
    `test_every_priced_result_actually_has_a_known_price` does.
    """
    from services.recommendation import _gutenberg_to_api, _ml_to_api, fuse_and_rank

    catalogue_row = {
        "id": 1, "title": "A Catalogue Book", "author": "Someone",
        "genre": "Fiction", "language": "en", "page_count": 200,
        "average_rating": 4.0, "ratings_count": 10, "list_price": 0.0,
        "book_id": "9876543", "thumbnail": "https://images.gr-assets.com/x.jpg",
        "final_score": 0.9,
    }
    local = [_ml_to_api(catalogue_row, 1)]
    gutenberg = [_gutenberg_to_api(GUTENBERG_SEARCH_RESULT, 1)]

    fused = fuse_and_rank(local, gutenberg, top_n=12)
    assert len(fused) == 2
    for book in fused:
        # The exact line that crashed: no .get(), no default.
        assert book["availability"] in {"free_public_domain", "listed", "unknown"}
