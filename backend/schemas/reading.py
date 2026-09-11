"""Request models for reading progress and reminders — ProgressRequest,
ReminderRequest.

Extracted verbatim from `main.py` in the Phase D restructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ProgressRequest(BaseModel):
    user_id: str = "guest"
    book_id: Optional[int] = None
    progress: Optional[float] = None
    total_pages: Optional[int] = None


class ReminderRequest(BaseModel):
    user_id: str = "guest"
    book_id: int
    enabled: bool = True
