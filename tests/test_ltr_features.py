"""F-26 — the LTR must be trained on the features it is served.

Feature importances before this fix:

    log_ratings        0.674
    log_ratings_norm   0.314
    avg_rating         0.013
    content_s          0.000      <- ones at training
    genre_pop_s               0.000      <- ones at training
    cluster_match      0.000      <- zeros at training
    mood_match         0.000      <- zeros at training
    inv_price          0.000
    comment_score      0.000
    recency            0.000

Five of those ten features are *query-dependent*: they only mean anything
relative to a seed. Training pointwise over the catalogue left no seed, so
four were filled with `ones` and `zeros`. A gradient-boosted tree never splits
on a constant column, so they were dead weight at training and live inputs at
inference — train/serve skew, not an oversight.

The fix samples seeds and builds each training row relative to its seed using
`LearningToRank._features`, *the same function inference calls*. Sharing the
function is what stops the skew coming back: two parallel implementations
would drift the first time either changed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FEATURES = [
    "content_s", "genre_pop_s", "cluster_match", "avg_rating", "log_ratings_norm",
    "inv_price", "recency", "mood_match", "comment_score", "log_ratings",
]

# Two columns are constant for *data* reasons rather than construction ones.
# The distinction is the whole point of F-26: a column filled with `ones` is a
# bug, a column that is uniform because the data is uniform is not.
#
#   inv_price      1/(1+list_price), and every list_price is 0 (F-36 — no
#                  availability provider, so no real prices exist).
#   comment_score  zero for every book when no comments exist. The suite
#                  truncates `comments`, so it is always constant here.
#                  Measured at std 0.00365 against a database holding four.
#
# Both will vary on their own once the underlying data does. Listed explicitly
# so this test asserts what it can control and says why it excuses the rest.
EXPECTED_CONSTANT = {"inv_price", "comment_score"}


@pytest.fixture(scope="module")
def training_matrix(recommender):
    X, y = recommender._ltr_training_set()
    return X, y


def test_the_query_dependent_features_actually_vary(training_matrix):
    """The heart of F-26. `ones` and `zeros` columns are invisible to a tree."""
    X, _ = training_matrix
    constant = {
        name for i, name in enumerate(FEATURES) if X[:, i].std() == 0.0
    }
    unexpected = constant - EXPECTED_CONSTANT
    assert not unexpected, (
        f"{sorted(unexpected)} are constant in the LTR training matrix, so the "
        "model cannot split on them. They are live inputs at inference — this "
        "is the train/serve skew F-26 recorded, reintroduced."
    )


@pytest.mark.parametrize("name", ["content_s", "genre_pop_s", "cluster_match", "mood_match"])
def test_each_previously_constant_feature_varies(training_matrix, name):
    """Named individually so a failure says which one regressed."""
    X, _ = training_matrix
    col = X[:, FEATURES.index(name)]
    assert col.std() > 0.0, f"{name} is constant at training time again"
    assert col.min() < col.max()


def test_training_and_inference_build_features_with_the_same_function(recommender):
    """Structural guard. Two implementations would drift; one cannot."""
    import inspect

    source = inspect.getsource(recommender._ltr_training_set)
    assert "self.ltr._features(" in source, (
        "the training matrix is no longer built by the same function "
        "inference uses — skew can return silently"
    )


def test_the_matrix_has_one_row_per_seed_candidate(training_matrix, recommender):
    X, y = training_matrix
    assert X.shape[1] == len(FEATURES)
    assert len(X) == len(y)
    # Seeds x candidates, minus any seed whose neighbourhood came back empty.
    assert len(X) <= recommender.LTR_SEEDS * recommender.LTR_PER_SEED


def test_with_no_readers_the_target_is_the_popularity_prior(training_matrix):
    """F-26, answered 2026-09-11: relevance is reading depth, shrunk towards a
    popularity prior by the amount of evidence.

    With no reading evidence — the state of the test database, and of every
    database until there is traffic — the shrinkage returns the prior exactly.
    So this asserts the target is still 0.4*rating + 0.6*norm(log count):
    not because that is the target any more, but because with zero readers it
    is what the target must reduce to. That is what keeps rankings unchanged
    until real reading data arrives.

    Formerly `test_the_target_is_still_circular_and_that_is_recorded`, a
    tripwire meant to fail when the target was replaced. It could not: with no
    data the new target and the old one are numerically identical. It is
    renamed and re-documented by hand for exactly that reason — a green test
    whose explanation has gone stale is the failure it existed to prevent.
    The shift towards depth is pinned in tests/test_reading_depth.py.
    """
    X, y = training_matrix
    rating = X[:, FEATURES.index("avg_rating")]
    counts = X[:, FEATURES.index("log_ratings")]
    counts_norm = counts / (counts.max() + 1e-9)
    prior = 0.4 * rating + 0.6 * counts_norm
    assert np.allclose(y, prior, atol=1e-6), (
        "with no reading evidence the target should equal the popularity "
        "prior exactly — either the test database now has reading events, or "
        "the shrinkage no longer reduces to the prior at zero readers"
    )
