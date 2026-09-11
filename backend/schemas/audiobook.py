"""Request model for /api/audiobook/generate — AudiobookRequest.

Extracted verbatim from `main.py` in the Phase D restructure.

test_security.py's test_schema_matches_source guard follows this move —
see the retarget in that commit.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AudiobookRequest(BaseModel):
    # F-04: `output_file` used to be accepted from the client and passed
    # straight into os.remove() and open(..., "ab"), so a request carrying
    # {"output_file": "../server.js"} deleted and overwrote the server.
    #
    # The field is gone. The destination is now derived server-side from
    # book_id (see AudiobookEngine.generate). extra="forbid" makes a request
    # that still sends output_file fail closed with 422 rather than have it
    # silently ignored — an old client gets a clear error, not a false success.
    model_config = ConfigDict(extra="forbid")

    book_id: int = Field(..., ge=1)
    book_name: str = Field("Animal Farm", min_length=1, max_length=300)
    language: str = Field("en", min_length=2, max_length=5)
