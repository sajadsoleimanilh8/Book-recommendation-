"""F-26's answer, part 2 — reading depth as the relevance target.

Decided 2026-09-11. The product owner chose how far readers get into a book
as the definition of a good recommendation, because it was the only signal
capturable today and measures reading rather than curiosity.

These pin three things:

* the depth maths, including the corrections that stop it lying;
* the shrinkage: no readers means the old ranking, evidence moves it;
* that depth is used as the target and never leaks into the features.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.reading_depth import (  # noqa: E402
    PRIOR_STRENGTH,
    aggregate,
    depth_from_page_counts,
    shrink,
)


# --- the depth maths ------------------------------------------------------


@pytest.mark.parametrize(
    "counts, pages, expected",
    [
        pytest.param({1: 10}, 5, 0.0, id="everyone-bounces-after-page-one"),
        pytest.param({p: 10 for p in range(1, 6)}, 5, 1.0, id="everyone-finishes"),
        pytest.param({1: 10, 2: 5, 3: 5, 4: 5, 5: 5}, 5, 0.5, id="half-and-half"),
    ],
)
def test_depth_matches_a_hand_calculation(counts, pages, expected):
    depth, readers = depth_from_page_counts(counts, pages)
    assert depth == pytest.approx(expected)
    assert readers == 10


def test_re_reading_an_earlier_page_cannot_manufacture_depth():
    """Flipping back to page 3 adds a page-3 request with nobody going
    further. Uncapped, this example scores 0.525; the monotone cap gives 0.4,
    which is what the readers actually did."""
    depth, _ = depth_from_page_counts({1: 10, 2: 4, 3: 9, 4: 4, 5: 4}, 5)
    assert depth == pytest.approx(0.4)


def test_a_book_nobody_opened_is_unmeasured_not_zero():
    """Reached only by deep link: no starting point. Scoring it 0.0 would say
    readers rejected it — the absent-as-negative mistake."""
    assert depth_from_page_counts({3: 6, 4: 6}, 5) == (0.0, 0)


def test_a_one_page_excerpt_is_finished_by_opening_it():
    assert depth_from_page_counts({1: 3}, 1) == (1.0, 3)


# --- aggregation from raw events -----------------------------------------


def _turn(page, excerpt=5, *, page_size=1, had_content=True):
    return {"page": page, "page_size": page_size,
            "excerpt_pages": excerpt, "had_content": had_content}


def test_only_genuine_page_turns_count():
    """Bulk fetches, empty pages past the excerpt, and pages beyond its end
    must all be ignored, or the pager's 'page 300 of 11' inflates depth."""
    book = ("gutenberg", "84")
    events = [
        (book, _turn(1)),
        (book, _turn(2, page_size=24)),          # bulk fetch, not a turn
        (book, _turn(40, had_content=False)),    # clicked past the excerpt
        (book, _turn(9, excerpt=5)),             # impossible page number
    ]
    assert aggregate(events) == {book: (0.0, 1)}


def test_books_are_aggregated_separately():
    a, b = ("gutenberg", "1"), ("gutenberg", "2")
    events = [(a, _turn(1)), (a, _turn(2)), (b, _turn(1))]
    result = aggregate(events)
    assert result[a][0] > result[b][0]


# --- shrinkage ------------------------------------------------------------


def test_with_no_readers_the_prior_is_returned_exactly():
    """This is what keeps every ranking unchanged until there is traffic."""
    assert shrink(0.9, 0, 0.42) == pytest.approx(0.42)


def test_evidence_moves_the_target_towards_depth():
    few = shrink(0.9, 1, 0.42)
    many = shrink(0.9, 1000, 0.42)
    assert 0.42 < few < many < 0.9 + 1e-9
    assert many == pytest.approx(0.9, abs=0.01)


def test_one_enthusiastic_reader_cannot_outweigh_the_prior():
    """A single reader who finished is less evidence than PRIOR_STRENGTH."""
    assert shrink(1.0, 1, 0.2) < (1.0 + 0.2) / 2


def test_prior_strength_is_the_halfway_point():
    assert shrink(1.0, PRIOR_STRENGTH, 0.0) == pytest.approx(0.5)


# --- wired into the ranker -----------------------------------------------


def test_the_ranker_target_uses_depth_when_it_exists(recommender):
    """End to end through Recommender._relevance, the real target function."""
    cands = recommender.df.head(4).copy()
    scale = float(np.log1p(recommender.df["ratings_count"].to_numpy(float)).max()) + 1e-9

    cands["reading_depth"], cands["reading_depth_n"] = 0.0, 0
    prior = recommender._relevance(cands, scale)

    cands["reading_depth"], cands["reading_depth_n"] = 1.0, 500
    with_evidence = recommender._relevance(cands, scale)

    assert np.all(with_evidence > prior), "evidence of full reading did not raise relevance"
    assert np.allclose(with_evidence, 1.0, atol=0.02)


def test_depth_never_reaches_the_feature_matrix(recommender):
    """Leakage guard, behavioural. If depth were a feature as well as the
    target, the model could learn 'relevance equals this column' and nothing
    else — a perfect training score and no learning at all.

    Plant a value nothing else could produce and require it to be absent.
    """
    marker = 0.123456789
    cands = recommender.df.head(8).copy()
    cands["reading_depth"], cands["reading_depth_n"] = marker, 777

    X = recommender.ltr._features(
        cands, np.full(len(cands), 0.5), np.full(len(cands), 0.5), 0, []
    )
    assert not np.any(np.isclose(X, marker)), "reading_depth leaked into the features"
    assert not np.any(np.isclose(X, 777)), "reading_depth_n leaked into the features"
