"""Request model for /feedback — FeedbackRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    user_id: str = "guest"
    book_id: int
    rating: float = Field(..., ge=1, le=5)
