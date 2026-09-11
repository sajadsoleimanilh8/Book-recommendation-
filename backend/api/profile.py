"""User taste profile — GET /api/profile/{user_id}.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
error_response and get_profile become main.<name> (RESTRUCTURE-NOTES
B-4, 5.2).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, status

import main
from auth import CurrentUser

router = APIRouter()


@router.get("/api/profile/{user_id}")
def get_user_profile(user_id: str, user: CurrentUser) -> Dict[str, Any]:
    """F-07 read-authz sweep: this exposed any user's taste profile, mood,
    viewing history and keyword affinities to any anonymous caller who could
    guess a user_id — and the default was the literal string "guest".

    A user may now read only their own profile. 403 rather than 404 because
    the caller has proven identity; the resource plainly exists.
    """
    if user_id != str(user.id):
        return main.error_response(
            "You can only view your own profile.", status.HTTP_403_FORBIDDEN
        )

    p = main.get_profile(str(user.id))
    return {
        "user_id": user_id,
        "mood": p.mood,
        "preferred_genres": p.preferred_genres,
        "preferred_authors": p.preferred_authors,
        "viewed_books": p.viewed_books[-20:],
        "exploration_rate": round(p.exploration_rate, 4),
        "has_taste_vector": p.taste_vector is not None,
        "taste_vector_dim": len(p.taste_vector) if p.taste_vector is not None else 0,
        "feedback_given": len(p.rating_history),
        "reading_speed_ppm": round(p.reading_speed_ppm, 2),
        "liked_keywords": p.liked_keywords[-10:],
        "disliked_keywords": p.disliked_keywords[-10:],
    }

