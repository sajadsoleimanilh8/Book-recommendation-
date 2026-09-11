"""Request model for /chatbot and /api/chat — ChatbotRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChatbotRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = "guest"
    book_id: Optional[int] = None
