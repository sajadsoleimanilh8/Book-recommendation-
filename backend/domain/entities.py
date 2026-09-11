"""Domain entities — the dataclasses the engine and the API pass around.

Extracted verbatim from `engine.py` in the Phase C restructure. No
behaviour change: same fields, same defaults, same order.

This module is deliberately dependency-free apart from numpy (which
`UserProfile.taste_vector` is typed against). Nothing here imports
fastapi, sqlalchemy, or anything under `services/` — these types are
shared by the ML layer, the service layer and the API layer, so they
must not drag any of them in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class UserProfile:
    preferred_genres: list[str] = field(default_factory=list)
    preferred_authors: list[str] = field(default_factory=list)
    mood: str = ""
    rating_history: dict[str, float] = field(default_factory=dict)
    viewed_books: list[str] = field(default_factory=list)
    taste_vector: Optional[np.ndarray] = None
    exploration_rate: float = 0.15
    liked_keywords: list[str] = field(default_factory=list)
    disliked_keywords: list[str] = field(default_factory=list)
    reading_speed_ppm: float = 0.0


@dataclass
class Comment:
    user_id: str
    text: str
    rating: Optional[int]
    sentiment: str
    keywords: list[str]
    timestamp: str
    embedding: Optional[list[float]] = None


@dataclass
class Reminder:
    book_id: int
    title: str
    user_id: str
    enabled: bool
    last_notified: int
    last_message: str
    last_fired_at: str
    created_at: str
    eta_minutes: Optional[float] = None


__all__ = ["UserProfile", "Comment", "Reminder", "MOOD_GENRE_MAP"]

# Shared reference data, not a dataclass, but placed here for the same
# reason: ChatbotEngine (services/chat.py), Recommender and
# QuestionerEngine (still in engine.py, pending their own Phase C
# extraction) all read this mapping, and none of the three may depend
# on either of the other two. Moved out of engine.py ahead of
# ChatbotEngine's extraction to avoid a circular import between
# services.chat and engine (RESTRUCTURE-PROMPT's "solve with
# TYPE_CHECKING or pass the instance in, not by merging back together"
# — same principle, applied to a shared constant instead of a class).

MOOD_GENRE_MAP: dict[str, list[str]] = {
    "happy": ["Comedy", "Humor", "Feel-Good", "Young Adult"],
    "sad": ["Drama", "Tragedy", "Poetry", "Literary Fiction"],
    "adventurous": ["Adventure", "Fantasy", "Action", "Sci-Fi"],
    "romantic": ["Romance", "Drama", "Chick Lit"],
    "motivational": ["Self-Help", "Biography", "Personal Development"],
    "dark": ["Horror", "Thriller", "Mystery", "Noir"],
    "relaxing": ["Travel", "Nature", "Philosophy", "Essays"],
    "thoughtful": ["Philosophy", "Science", "History", "Psychology"],
    "curious": ["Science", "Technology", "Mathematics"],
    "nostalgic": ["Classic", "Historical Fiction", "Memoir"],
}
