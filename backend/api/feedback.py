"""Explicit rating feedback — POST /feedback.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
RECOMMENDER, error_response, BOOK_BY_ID, get_profile and DF become
main.<name> — main.py's live module state (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd
from fastapi import APIRouter, status

import events
import main
import store
from db import SessionLocal
from schemas.feedback import FeedbackRequest

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/feedback")
@router.post("/api/feedback")
def feedback(payload: FeedbackRequest) -> Dict[str, Any]:
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, '_fitted', False):
        return main.error_response("ML engine not ready.")

    b = main.BOOK_BY_ID.get(payload.book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    profile = main.get_profile(payload.user_id)
    matches = main.DF[main.DF["title"] == b["title"]] if main.DF is not None else pd.DataFrame()
    if not matches.empty:
        try:
            main.RECOMMENDER.record_feedback(int(matches.index[0]), payload.rating, profile)
        except Exception as e:
            log.error(f"Error recording feedback: {e}")

    # F-42. An explicit rating is the strongest signal in the product and the
    # only one that is unambiguous about direction. It currently updates an
    # in-memory taste vector that dies with the process; this makes it
    # durable, which is what a retrained model would need.
    with SessionLocal() as _s:
        _pk = store.resolve_book_pk(_s, b)
    events.record(
        events.FEEDBACK,
        book_id=_pk,
        context={"rating": payload.rating, "profile_user": payload.user_id},
    )

    return {
        "ok": True,
        # Waiting-on-you #3 (PROGRESS.md): this used to say "taste model
        # updated" and report taste_vector_dim, which is always 0 —
        # `UserProfile.taste_vector` is never assigned anywhere in the
        # codebase (F-46). The claim described a feature that does not
        # exist. What actually happens on a rating: the bandit's exploration
        # rate adjusts (below) and the rating is logged durably
        # (`events.record`, F-42) for future model training. Said plainly
        # rather than removed silently, so a caller does not have to guess
        # why the old fields disappeared.
        "message": "Feedback recorded.",
        "exploration_rate": round(profile.exploration_rate, 4),
    }


