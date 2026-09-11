"""Approximate nearest-neighbour similarity — the SimilarityEngine.

Extracted verbatim from `engine.py` in the Phase C restructure.
Pure model code: numpy, sklearn and logging only.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from sklearn.neighbors import NearestNeighbors

log = logging.getLogger(__name__)


class SimilarityEngine:
    def __init__(self, n_neighbors: int = 60):
        self.nn = NearestNeighbors(n_neighbors=n_neighbors, metric="cosine",
                                   algorithm="brute", n_jobs=-1)
        self._X: Optional[np.ndarray] = None

    def fit(self, X: np.ndarray):
        self._X = X
        self.nn.fit(X)
        log.info("ANN similarity engine ready.")

    def query(self, idx: int, k: int = 20) -> tuple[np.ndarray, np.ndarray]:
        dists, indices = self.nn.kneighbors(self._X[idx].reshape(1, -1), n_neighbors=k + 1)
        mask = indices[0] != idx
        return indices[0][mask][:k], 1.0 - dists[0][mask][:k]

    def query_vector(self, vec: np.ndarray, k: int = 20) -> tuple[np.ndarray, np.ndarray]:
        dists, indices = self.nn.kneighbors(vec.reshape(1, -1), n_neighbors=k)
        return indices[0], 1.0 - dists[0]
