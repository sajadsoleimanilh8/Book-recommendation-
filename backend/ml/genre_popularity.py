"""Genre-popularity item factors — renamed from `CollaborativeFilter`, F-14.

**This is not collaborative filtering and never was.** Collaborative filtering
factorises a *user*-item matrix: books are similar because the same people
engaged with both. The matrix built below is `genre x book`, and the only
value in it is `average_rating * log1p(ratings_count)` — popularity. There is
no user dimension anywhere in it, so two books come out "similar" when they
share a genre and have comparable popularity.

That is a real signal and it is kept, at its existing weight. What is gone is
the name, which claimed something the code has never done: F-26's
feature-importance table read `cf_s` as evidence about collaborative
filtering, and it was evidence about genre popularity. Real CF needs real
multi-user interaction data, which this project does not yet have — see F-14
for the measured state and the unblock condition.

`user_vector()` is kept for the same reason and with the same caveat: nothing
populates `liked_indices` today (`taste_vector` is never assigned, F-46), so
it is reachable but unused.

Extracted verbatim from `engine.py` in the Phase C restructure.
Pure model code: numpy, pandas, scipy, sklearn and logging only.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import LabelEncoder

log = logging.getLogger(__name__)


class GenrePopularityFactors:
    def __init__(self, n_factors: int = 15):
        self.svd = TruncatedSVD(n_components=n_factors, random_state=42)
        self.item_factors: Optional[np.ndarray] = None

    def fit(self, df: pd.DataFrame):
        signal = (df["average_rating"].values * np.log1p(df["ratings_count"].values))
        le = LabelEncoder()
        gids = le.fit_transform(df["genre"].astype(str).to_numpy())
        mat = csr_matrix((signal, (gids, np.arange(len(df)))),
                         shape=(len(le.classes_), len(df)))
        self.item_factors = self.svd.fit_transform(mat.T)
        log.info(f"genre-popularity item factors: {self.item_factors.shape}")

    def similar_items(self, idx: int, k: int = 20) -> np.ndarray:
        if self.item_factors is None:
            raise RuntimeError("genre-popularity factors not fitted.")
        sims = self._cosine_sim_1d(self.item_factors[idx], self.item_factors)
        sims[idx] = -1.0
        return np.argsort(sims)[::-1][:k]

    def user_vector(self, liked_indices: list[int]) -> np.ndarray:
        if not liked_indices or self.item_factors is None:
            return np.zeros(self.svd.n_components)
        return self.item_factors[liked_indices].mean(axis=0)

    @staticmethod
    def _cosine_sim_1d(vec: np.ndarray, mat: np.ndarray) -> np.ndarray:
        n = np.linalg.norm(vec)
        if n == 0:
            return np.zeros(len(mat))
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1e-9
        return (mat @ vec) / (norms * n)
