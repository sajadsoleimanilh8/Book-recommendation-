"""Gradient-boosted learning-to-rank — the LearningToRank model.

Extracted verbatim from `engine.py` in the Phase C restructure.
Pure model code: numpy, pandas, sklearn and logging only.

Carries F-26's fix and its explanation intact (`train()`'s docstring):
the caller must pass a prepared (X, y) rather than have this class
fabricate the query-dependent features as constants.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import train_test_split

log = logging.getLogger(__name__)


class LearningToRank:
    def __init__(self):
        self.model = GradientBoostingRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=4,
            subsample=0.8,
            random_state=42
        )
        self.fitted = False

    def _features(self, cands, content_s, cf_s, cluster_id, mood_genres) -> np.ndarray:
        mood_match = cands["genre"].apply(
            lambda g: float(any(m.lower() in str(g).lower() for m in mood_genres))
        ).values

        cluster_match = (cands["cluster"].values == cluster_id).astype(float)
        recency = cands.get("recency_weight", pd.Series(0.5, index=cands.index)).values
        comment_score = cands.get("comment_score", pd.Series(0.0, index=cands.index)).values

        return np.column_stack([
            content_s,
            cf_s,
            cluster_match,
            cands["average_rating"].values / 5.0,
            np.log1p(cands["ratings_count"].values) / 15.0,
            1.0 / (1.0 + cands["list_price"].values),
            recency,
            mood_match,
            comment_score,
            np.log1p(cands["ratings_count"].values),
        ])

    def train(self, X: np.ndarray, y: np.ndarray):
        """Fit on a prepared (X, y) — F-26.

        The caller builds the matrix, because five of the ten features are
        *query-dependent* and this class has no query. It used to accept
        `(df, content_s, cf_s)` and fill the rest with `ones` and `zeros`,
        which is how `content_s`, `cf_s`, `cluster_match` and `mood_match`
        came to be constant columns. A gradient-boosted tree never splits on
        a constant, so those four features were dead weight at training and
        live inputs at inference — a train/serve skew, not an oversight.
        """
        if len(X) < 20:
            # score() already falls back to a fixed blend when unfitted, so
            # refusing is better than fitting on noise and looking trained.
            log.warning(f"LTR: only {len(X)} training rows; leaving unfitted")
            self.fitted = False
            return

        X_tr, X_val, y_tr, y_val = train_test_split(
            X, y, test_size=0.15, random_state=42
        )
        self.model.fit(X_tr, y_tr)
        log.info(
            f"LTR trained on {len(X):,} seed-relative rows; "
            f"val R2 {self.model.score(X_val, y_val):.4f}"
        )
        self.fitted = True

    def score(self, cands, content_s, cf_s, cluster_id, mood_genres) -> np.ndarray:
        X = self._features(cands, content_s, cf_s, cluster_id, mood_genres)

        if self.fitted:
            return self.model.predict(X)

        return (
            content_s * 0.4 +
            cf_s * 0.3 +
            cands["average_rating"].values / 5.0 * 0.3
        )
