"""Book clustering — the ClusteringModel.

Extracted verbatim from `engine.py` in the Phase C restructure.
Pure model code: numpy, sklearn and logging only.
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.cluster import MiniBatchKMeans

log = logging.getLogger(__name__)


class ClusteringModel:
    def __init__(self, k_range: range = range(4, 15)):
        self.k_range = k_range
        self.model: Optional[MiniBatchKMeans] = None
        self.best_k: int = 8

    def fit(self, X: np.ndarray) -> np.ndarray:
        self.best_k = self._find_elbow(X)
        self.model = MiniBatchKMeans(n_clusters=self.best_k, random_state=42,
                                     n_init=10, batch_size=2048)
        labels = self.model.fit_predict(X)
        log.info(f"KMeans: best k={self.best_k}")
        return labels

    def _find_elbow(self, X: np.ndarray) -> int:
        rng = np.random.default_rng(0)
        n_samples = min(3000, len(X))
        sample = X[rng.choice(len(X), n_samples, replace=False)]
        inertias = []
        for k in self.k_range:
            m = MiniBatchKMeans(n_clusters=k, random_state=42, n_init=3)
            m.fit(sample)
            inertias.append(m.inertia_)
        d2 = np.diff(np.diff(inertias))
        best = int(np.argmax(d2)) + self.k_range.start + 1
        return int(np.clip(best, self.k_range.start, self.k_range.stop - 1))
