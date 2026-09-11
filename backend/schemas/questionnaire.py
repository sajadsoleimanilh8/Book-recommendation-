"""Request model for /questionnaire — QuestionnaireRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class QuestionnaireRequest(BaseModel):
    user_id: str = "guest"
    genre: Optional[str] = Field(None, example="fantasy")
    mood: Optional[str] = Field(None, example="dark")
    pace: Optional[str] = Field(None, example="fast", description="slow | fast | any")
    language: Optional[str] = Field(None, example="en", description="en | fa | fr | de | es | any")
    popularity: Optional[str] = Field(None, example="popular", description="popular | underrated | any")
    favorite_author: Optional[str] = Field(None, example="James Clear")
    top_k: int = Field(10, ge=1, le=50)
