"""Request models for /filter and /recommend — FilterRequest, RecommendRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class FilterRequest(BaseModel):
    genre: Optional[str] = None
    rating_min: Optional[float] = None
    language: Optional[str] = None
    mood: Optional[str] = None
    max_price: Optional[float] = None


class RecommendRequest(BaseModel):
    user_id: str = "guest"
    # F-47. English by default, matching the product decision recorded in
    # docs/PROGRESS.md. "any" opts out. Confirmed non-English books are
    # excluded; books with no language data recorded are not (see
    # Recommender.recommend_by_profile) — absence of a language label is
    # not evidence the book isn't English.
    language: str = "en"
    max_price: Optional[float] = None
    genre: Optional[str] = None
    favorite_author: Optional[str] = None
    favorite_book: Optional[str] = None
    feeling: Optional[str] = None
    min_rating: Optional[float] = None
    min_pages: Optional[int] = None
    max_pages: Optional[int] = None
    top_k: int = 12
