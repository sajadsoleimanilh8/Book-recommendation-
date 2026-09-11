"""Compatibility facade — engine.py after the Phase C restructure.

`backend/engine.py` used to be a 1,722-line file holding the entire ML
system: DataLoader, seven pure model classes, a Gutenberg client, four
sub-engines, and Recommender/QuestionerEngine. All of it has moved —
see RESTRUCTURE-NOTES.md for the full move-by-move record — and this
file now only re-exports the five names real code still reaches through
it: `backend/main.py:42` and `tests/test_golden_ranking.py` (UserProfile,
x4) both do `from engine import ...`, and RESTRUCTURE-PROMPT section 4
promises this file keeps resolving with no edit to either.

Real import, not a sys.modules alias like the other Phase B/C shims:
this module never had a 1:1 replacement (its contents scattered across
seven files), so there is no single "the real module" to alias to.
"""

from domain.entities import UserProfile
from ml.data_loader import DataLoader
from services.providers.gutenberg import GutenbergClient
from services.recommendation import QuestionerEngine, Recommender

__all__ = ["DataLoader", "GutenbergClient", "Recommender", "QuestionerEngine", "UserProfile"]
