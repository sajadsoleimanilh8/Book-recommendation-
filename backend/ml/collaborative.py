"""Item-factor collaborative filtering — the CollaborativeFilter.

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


class CollaborativeFilter:
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
        log.info(f"CF item factors: {self.item_factors.shape}")

    def similar_items(self, idx: int, k: int = 20) -> np.ndarray:
        if self.item_factors is None:
            raise RuntimeError("CF not fitted.")
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
