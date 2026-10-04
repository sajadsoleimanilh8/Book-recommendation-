"""Ratings nobody gave — F-67, section 18 applied to ratings.

**64.5% of the catalogue — 19,342 of 29,975 rows — carried a rating nobody
gave.** Two separate fabrications:

* **13,035 google_books rows** hold `average_rating = 4.0035747133` with
  `ratings_count = 0`. A rating with zero ratings cannot be anything but
  invented, and that constant sits within 0.001 of the mean of every rated
  row, so it is mean imputation.
* **All 6,307 gutenberg rows** hold exactly `4.0` and `100` — identical for
  every one, so the count is invented too.

`/books` sorted by this and `rating_min` filtered on it, so ranking and
filtering were both driven by imputed numbers while the UI printed them as
fact. This is F-36's decision taken again: every row carried
`list_price = 0.00`, both serialisers rendered it as "Free", and the answer
was `None` and "unknown" rather than a plausible number.

The sort also had no evidence threshold at all: 251 rows hold 5.0 from a
single rating, and they outranked a book with 4,780,653 ratings at 4.5.
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

needs_db = pytest.mark.skipif(not HAVE_DB, reason="database unavailable")

GUTENBERG = {"source": "gutenberg", "average_rating": 4.0, "ratings_count": 100}
IMPUTED = {"source": "google_books", "average_rating": 4.0035747133, "ratings_count": 0}
ONE_RATING = {"source": "google_books", "average_rating": 5.0, "ratings_count": 1}
ESTABLISHED = {"source": "goodreads", "average_rating": 4.5, "ratings_count": 4_780_653}


# -- what counts as a rating ----------------------------------------------


def test_a_rating_with_no_ratings_is_not_a_rating():
    """The 13,035 imputed rows. `ratings_count = 0` is the tell, and it is
    checked rather than the constant, so a different imputed value would
    still be caught."""
    from services.catalogue import rating_and_count

    assert rating_and_count(IMPUTED) == (None, None)
    assert rating_and_count({**IMPUTED, "average_rating": 3.7}) == (None, None)


def test_gutenberg_is_excluded_by_source_not_by_matching_the_placeholder():
    """Project Gutenberg has no rating system, so no Gutenberg row can carry
    a real rating. That is a fact about the source rather than about today's
    data, and it stays true if the placeholder value ever changes."""
    from services.catalogue import rating_and_count

    assert rating_and_count(GUTENBERG) == (None, None)
    # A different placeholder, same answer.
    assert rating_and_count({**GUTENBERG, "average_rating": 4.4, "ratings_count": 7}) == (
        None,
        None,
    )


def test_a_real_rating_survives():
    from services.catalogue import rating_and_count

    assert rating_and_count(ESTABLISHED) == (4.5, 4_780_653)
    assert rating_and_count(ONE_RATING) == (5.0, 1)


def test_a_count_with_no_rating_is_as_incoherent_as_the_reverse():
    from services.catalogue import rating_and_count

    assert rating_and_count({"source": "goodreads", "average_rating": 0, "ratings_count": 9}) == (
        None,
        None,
    )


# -- the evidence threshold -----------------------------------------------


def test_a_single_five_star_cannot_outrank_a_million_ratings():
    """The specific defect: 251 rows hold 5.0 from one rating and sorted
    above 4,780,653 ratings at 4.5."""
    from services.catalogue import weighted_rating

    thin = weighted_rating(ONE_RATING)
    thick = weighted_rating(ESTABLISHED)

    assert thin < thick, f"{thin} should rank below {thick}"
    assert thin == pytest.approx(4.026, abs=0.01)


def test_shrinkage_barely_touches_an_established_rating():
    """The threshold has to tame thin evidence without rewriting thick
    evidence. The 10th percentile of real rating counts is 8,397."""
    from services.catalogue import weighted_rating

    established = {"source": "goodreads", "average_rating": 4.2, "ratings_count": 8_397}
    assert weighted_rating(established) == pytest.approx(4.2, abs=0.005)


def test_an_unrated_book_has_no_weighted_rating_either():
    from services.catalogue import weighted_rating

    assert weighted_rating(GUTENBERG) is None
    assert weighted_rating(IMPUTED) is None


def test_the_shrinkage_is_the_reading_depth_one_not_a_second_copy():
    """`shrink` was written for the reading target and its docstring already
    states the property this needs. Two implementations of one blend is how
    F-36 and F-54 happened."""
    from services.catalogue import NEUTRAL_RATING, RATING_PRIOR_STRENGTH, weighted_rating
    from services.reading_depth import shrink

    assert weighted_rating(ESTABLISHED) == shrink(
        4.5, 4_780_653.0, NEUTRAL_RATING, RATING_PRIOR_STRENGTH
    )


# -- the constant must not go stale ---------------------------------------


@needs_db
def test_the_neutral_rating_still_matches_the_data():
    """`NEUTRAL_RATING` is a measured constant, not a value recomputed at
    load time, so that adding books cannot quietly move every ranking. The
    cost of that choice is staleness, so it is measured here instead of
    trusted — the F-26a pattern, which re-verified its numbers against the
    database rather than restating them.
    """
    import statistics

    from services.catalogue import NEUTRAL_RATING, load_books_raw, rating_and_count

    real = [
        r for r, _ in (rating_and_count(b) for b in load_books_raw(limit=None))
        if r is not None
    ]
    assert len(real) > 1000, f"only {len(real)} rated rows; has the catalogue changed?"

    measured = statistics.mean(real)
    assert measured == pytest.approx(NEUTRAL_RATING, abs=0.05), (
        f"NEUTRAL_RATING is {NEUTRAL_RATING} but the catalogue's real ratings "
        f"now average {measured:.4f}. Update the constant and re-baseline, or "
        "decide the drift is acceptable and widen this tolerance deliberately."
    )


@needs_db
def test_the_scale_of_the_problem_is_still_what_was_recorded():
    """Documents *why* the rule exists, against the live database. If this
    fails, the catalogue's rating coverage changed and F-67's reasoning
    should be re-read rather than the test adjusted."""
    from services.catalogue import load_books_raw, rating_and_count

    books = load_books_raw(limit=None)
    unrated = sum(1 for b in books if rating_and_count(b)[0] is None)

    assert unrated / len(books) == pytest.approx(0.645, abs=0.02), (
        f"{unrated} of {len(books)} rows are unrated; F-67 recorded 19,342 "
        "of 29,975 (64.5%)"
    )


# -- the API ---------------------------------------------------------------


@needs_db
def test_the_api_reports_null_rather_than_a_number():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        gutenberg = client.get("/books?limit=5&audiobook=true").json()["items"]

    assert gutenberg
    for book in gutenberg:
        assert book["rating"] is None, book["title"]
        assert book["ratings_count"] is None, book["title"]


@needs_db
def test_the_listing_leads_with_books_that_have_real_standing():
    """Before: the top of `/books` was rows holding 5.0 from one or two
    ratings. After: books with enough ratings to mean something."""
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        top = client.get("/books?limit=10").json()["items"]

    assert all(b["rating"] is not None for b in top), "an unrated book reached the top 10"
    assert min(b["ratings_count"] for b in top) > 1000, (
        "a book with almost no ratings is still leading the listing"
    )


@needs_db
def test_unrated_books_sort_last_but_are_not_dropped():
    """This is a catalogue listing. A book with no ratings is still a book."""
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        everything = client.get("/books?limit=500").json()
        total = everything["total"]

    # Every row is still reachable...
    assert total > 29_000, total
    # ...and none of the unrated ones are near the front.
    assert all(b["rating"] is not None for b in everything["items"][:100])


@needs_db
def test_rating_min_excludes_books_with_no_rating():
    """A book nobody has rated does not have a rating of at least anything.
    OI-11's argument for prices: `_safe_float(None) == 0.0` made
    `max_price=5` match all 29,975 rows, and a filter that quietly includes
    what it cannot judge is worse than one that is absent.
    """
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        for threshold in ("0", "0.0", "3.5", "4.5"):
            items = client.get(f"/books?limit=50&rating_min={threshold}").json()["items"]
            assert items, f"rating_min={threshold} returned nothing"
            assert all(b["rating"] is not None for b in items), (
                f"rating_min={threshold} included an unrated book"
            )


@needs_db
def test_both_serialisers_agree():
    """F-36 and F-54 both came from two payload builders answering one
    question differently."""
    from services.catalogue import _row_to_book
    from services.recommendation import _ml_to_api

    for row in (GUTENBERG, IMPUTED, ONE_RATING, ESTABLISHED):
        full = {**row, "title": "x", "page_count": 100, "rating": row["average_rating"]}
        catalogue = _row_to_book(0, full)
        ml = _ml_to_api(full, 1)
        assert catalogue["rating"] == ml["rating"], row
        assert catalogue["ratings_count"] == ml["ratings_count"], row


# -- the model sees numbers, the reader sees null --------------------------


@needs_db
def test_the_feature_columns_carry_no_nan():
    """A `None` in these columns becomes NaN and GradientBoostingRegressor
    refuses to fit, which silently disabled the whole recommender the first
    time `list_price` was changed to `None` (F-36's note). So an unrated book
    presents to the model as zero ratings at the catalogue mean.
    """
    import pandas as pd

    from services.catalogue import NEUTRAL_RATING, _books_to_df, load_books_raw

    # The whole catalogue, not a prefix. A 4,000-row sample contained no
    # unrated books at all — every google_books row that early carries a
    # real count, and the 13,035 count-0 rows plus all 6,307 Gutenberg rows
    # sit further down the file. The sample passed the NaN assertions and
    # then had nothing to check the substitution against.
    df = _books_to_df(load_books_raw(limit=None))

    assert not df["average_rating"].isna().any(), "NaN in average_rating"
    assert not df["ratings_count"].isna().any(), "NaN in ratings_count"

    # And the substitution is the neutral one, not the old fabrications.
    neutral_rows = df[df["ratings_count"] == 0]
    assert len(neutral_rows) > 19_000, (
        f"only {len(neutral_rows)} rows present as unrated; F-67 recorded 19,342"
    )
    # Compared with an explicit tolerance: `Series == pytest.approx(scalar)`
    # does not broadcast the way it reads, and reported a failure here while
    # every row was in fact within 1e-6.
    assert (neutral_rows["average_rating"] - NEUTRAL_RATING).abs().max() < 1e-6

    # Nothing retains the old imputed constant.
    assert (df["average_rating"] - 4.0035747133).abs().min() > 1e-6


@needs_db
def test_no_fake_popularity_survives_into_the_model():
    """The Gutenberg placeholder's count of 100 was the part that mattered:
    it entered the LTR features as `log1p(100) = 4.6` and the relevance
    target's prior as real popularity, for 6,307 books nobody has rated."""
    from services.catalogue import _books_to_df, load_books_raw

    books = load_books_raw(limit=None)
    df = _books_to_df(books)

    gutenberg_rows = [i for i, b in enumerate(books) if b.get("source") == "gutenberg"]
    assert len(gutenberg_rows) > 6000

    counts = df.iloc[gutenberg_rows]["ratings_count"]
    assert (counts == 0).all(), "a Gutenberg book still carries a fabricated count"
