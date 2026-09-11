"""Epsilon-greedy exploration over ranked candidates — the ContextualBandit.

Extracted verbatim from `engine.py` in the Phase C restructure.
Pure model code: numpy only.
"""

from __future__ import annotations

import numpy as np


class ContextualBandit:
    def __init__(self, epsilon: float = 0.15):
        self.epsilon = epsilon
        self.rewards: dict[int, list[float]] = {}

    def select(self, ranked_indices, profile, n=10) -> np.ndarray:
        n_explore = max(1, int(n * profile.exploration_rate))
        n_exploit = n - n_explore

        exploit = ranked_indices[:n_exploit]
        pool = ranked_indices[n_exploit:]

        explore = (
            np.random.choice(pool, size=n_explore, replace=False)
            if len(pool) >= n_explore
            else pool
        )

        return np.concatenate([exploit, explore])

    def update(self, item_idx: int, reward: float):
        self.rewards.setdefault(item_idx, []).append(reward)

    def expected_reward(self, item_idx: int) -> float:
        r = self.rewards.get(item_idx, [])
        return float(np.mean(r)) if r else 0.5
