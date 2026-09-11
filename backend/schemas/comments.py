"""Request model for /comments — CommentRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CommentRequest(BaseModel):
    # user_id removed: the author is the authenticated caller (F-07).
    # extra="forbid" for the same reason as AudiobookRequest — a client that
    # still sends user_id gets a clear 422 rather than silently having its
    # value ignored while the server records someone else as the author.
    model_config = ConfigDict(extra="forbid")

    book_id: int
    comment: str = Field(..., min_length=1, max_length=1000)
    rating: Optional[int] = Field(None, ge=1, le=5)
