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
    # F-22 Phase D. Anonymous callers echo back the opaque token the server
    # minted for them; a signed-in caller's history is keyed on their account
    # and this is ignored, so it can never hand anyone someone else's thread
    # (see core.conversations.resolve_key). Not the transcript itself: the
    # history lives server-side, and a client-sent one could be rewritten.
    conversation_id: Optional[str] = Field(None, max_length=64)
