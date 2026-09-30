"""Request model for the Reading Copilot — section 32, Phase 5."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    # `extra="forbid"` for the same reason AudiobookRequest carries it: a
    # request sending a field this endpoint does not accept gets a 422 rather
    # than having it silently ignored. The field that must never appear here
    # is a user or account id — the caller's identity comes from the
    # authenticated request, never from the body (see api/copilot.py).
    model_config = ConfigDict(extra="forbid")

    # The book is a path parameter, not a body field, so there is nothing
    # here to disagree with it.
    question: str = Field(..., min_length=1, max_length=1000)
