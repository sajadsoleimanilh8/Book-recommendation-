"""F-47 — `recommend_by_profile` filters by language, English by default.

Product decision (docs/PROGRESS.md, F-47): `UserProfile.language` defaults to
`"en"`. Before this, the direct `/api/recommend` path had no way to express a
language preference at all — not because of a decision against it, but
because `UserProfile` and `RecommendRequest` were structurally missing the
field (F-47's original finding).

The critical property under test is *not* "English books get preferred" —
that part is the easy half. It is that a book with **no recorded language is
never excluded**, because absence of a label is not evidence the book isn't
English. Copying the questionnaire's own filter pattern verbatim would have
gotten this wrong: `"unknown".startswith("en")` is `False`, so a naive
default-on filter would have silently dropped 32.8% of the catalogue (9,842
mostly-Goodreads books with no language on record) from every recommendation,
by default, for nobody's benefit. That is the same "unknown is not a negative
signal" mistake OI-11 (price) and F-29 (provider lookups) already cost this
project real damage to learn — here reproduced in a filter that would have
applied even more broadly, since it runs unless the caller opts out.

`language_mask` is tested directly against a plain pandas Series, not through
`recommend_by_profile`. Found the hard way: that method's candidate pool is
capped at ~150 by the ANN shortlist and then reshuffled by the bandit's
exploration slots, so a book's presence or absence in the final output is not
a reliable signal about the filter alone — a first version of this test file
passed identically whether the pass-through logic was present or deliberately
removed, because the sample it happened to check landed inside the same noise
either way. The end-to-end tests here check only that the parameter reaches
the pipeline at all, not the pass-through property itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from domain.entities import UserProfile  # noqa: E402
from services.recommendation import language_mask  # noqa: E402


# --- language_mask: the property this fix exists for, tested directly ----


def test_a_confirmed_non_english_row_is_excluded():
    col = pd.Series(["English", "Spanish", "German", "It"])
    assert language_mask(col, "en").tolist() == [True, False, False, False]


def test_an_unrecorded_language_row_is_not_excluded():
    """The property this whole fix exists for. A naive port of the
    questionnaire's own filter gets this specific case wrong."""
    col = pd.Series(["English", "Unknown", "Spanish"])
    assert language_mask(col, "en").tolist() == [True, True, False]


def test_any_disables_the_filter_entirely():
    col = pd.Series(["English", "Unknown", "Spanish", "German"])
    assert language_mask(col, "any").tolist() == [True, True, True, True]


def test_an_empty_request_behaves_like_any():
    col = pd.Series(["English", "Spanish"])
    assert language_mask(col, "").tolist() == [True, True]


def test_matching_is_case_insensitive_and_prefix_based():
    """Mirrors normalize_language's output, which capitalises unmapped
    codes (e.g. "en-GB" -> "En-gb") rather than title-casing them fully."""
    col = pd.Series(["ENGLISH", "en-GB", "English (US)"])
    assert language_mask(col, "en").tolist() == [True, True, True]


def test_a_column_that_is_entirely_unrecorded_excludes_nothing():
    """Degenerate but real: if language data is missing catalogue-wide, the
    filter must not turn into "recommend nothing"."""
    col = pd.Series(["Unknown", "Unknown", "Unknown"])
    assert language_mask(col, "en").tolist() == [True, True, True]


# --- wiring: the field reaches the pipeline at all -------------------------


def test_default_profile_language_is_english():
    """This is what makes the filter on-by-default rather than opt-in."""
    assert UserProfile().language == "en"


def test_default_request_language_is_english():
    from schemas.recommend import RecommendRequest

    assert RecommendRequest().language == "en"


def test_the_recommend_endpoint_accepts_a_language_field(fitted_app):
    main, client = fitted_app
    response = client.post("/api/recommend", json={"top_k": 5})
    assert response.status_code == 200

    response = client.post("/api/recommend", json={"top_k": 5, "language": "any"})
    assert response.status_code == 200

    response = client.post("/api/recommend", json={"top_k": 5, "language": "de"})
    assert response.status_code == 200


def test_the_profile_carries_the_requested_language_into_the_ranker(fitted_app, monkeypatch):
    """The one true end-to-end check: that api/recommend.py actually copies
    payload.language onto the profile passed to the ranker, rather than the
    field existing on both ends but never being connected."""
    main, client = fitted_app
    seen: list[str] = []
    original = main.RECOMMENDER.recommend_by_profile

    def spy(profile, *a, **k):
        seen.append(profile.language)
        return original(profile, *a, **k)

    monkeypatch.setattr(main.RECOMMENDER, "recommend_by_profile", spy)

    client.post("/api/recommend", json={"top_k": 3, "language": "de"})
    assert seen == ["de"], (
        "RecommendRequest.language did not reach the UserProfile passed to "
        "recommend_by_profile — the field exists but is disconnected"
    )
