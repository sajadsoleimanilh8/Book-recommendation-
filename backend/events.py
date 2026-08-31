"""Interaction logging — F-42, the seed of the data moat (§54).

Why this is urgent rather than merely useful
--------------------------------------------
`interaction_events` and `recommendation_log` have existed since PR 2, with
indexes, and their own docstrings say they must fill *before* model work
because history cannot be backfilled. Nothing ever wrote to them. Every day
without this is a day of signal permanently gone — unlike the Google quota,
which only delays work that can still be done later.

It is also the prerequisite for F-26. The ranking target is currently
`0.4 * rating + 0.6 * log(ratings_count)`, a function of the model's own
inputs, and it cannot honestly be replaced until something real exists to
replace it with.

Three rules, all of which cost more to retrofit than to build in
--------------------------------------------------------------
1. **Logging must never break the request.** A recommendation that 500s
   because analytics failed is strictly worse than one nobody measured. Every
   entry point here swallows its exceptions and warns.

2. **A failed log must not poison the caller's transaction.** This is the F-30
   lesson applied one layer over: an `INSERT` that raises inside a shared
   session leaves it unusable, so the *next* statement — the one the user
   actually asked for — fails too. Events therefore write on their own
   session, never the request's.

3. **Anonymous is signal.** `user_id` is nullable on purpose. Dropping
   anonymous events would discard most of the traffic a young product has.
"""

from __future__ import annotations

import logging
from typing import Any

from db import SessionLocal
from models import InteractionEvent, RecommendationLog

log = logging.getLogger(__name__)

# A closed vocabulary. Free-form strings would leave the analysis to guess
# whether "view", "book_view" and "viewed" are the same thing.
SEARCH = "search"
BOOK_VIEW = "book_view"
RECOMMEND = "recommend"
FEEDBACK = "feedback"
COMMENT = "comment"
PROGRESS = "progress"

EVENT_TYPES = {SEARCH, BOOK_VIEW, RECOMMEND, FEEDBACK, COMMENT, PROGRESS}

# `context` is JSONB and tempting to overfill. Keep it to what a ranking model
# could actually use, and never put a raw request body in it — that is how
# tokens and emails end up in an analytics table.
MAX_CONTEXT_CHARS = 2000


def _trim(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    trimmed = {}
    for key, value in context.items():
        if isinstance(value, str) and len(value) > 400:
            value = value[:400]
        trimmed[key] = value
    return trimmed


def record(
    event_type: str,
    *,
    user_id: int | None = None,
    book_id: int | None = None,
    context: dict[str, Any] | None = None,
) -> bool:
    """Write one interaction. Returns whether it landed; never raises.

    The boolean is for tests and for a future health counter — callers in
    request paths deliberately ignore it, because there is nothing useful they
    could do about a failed write that would not make the response worse.
    """
    if event_type not in EVENT_TYPES:
        # A typo'd event type is a silent hole in the data six months later.
        log.warning(f"refusing to record unknown event_type {event_type!r}")
        return False
    try:
        with SessionLocal() as session:
            session.add(
                InteractionEvent(
                    user_id=user_id,
                    book_id=book_id,
                    event_type=event_type,
                    context=_trim(context),
                )
            )
            session.commit()
        return True
    except Exception as exc:
        log.warning(f"interaction log failed ({event_type}): {type(exc).__name__}: {exc}")
        return False


def record_recommendation(
    *,
    user_id: int | None = None,
    request: dict[str, Any] | None = None,
    shown: dict[str, Any] | None = None,
    model_version: str | None = None,
    latency_ms: int | None = None,
) -> bool:
    """Log what was asked for and what came back — the held-out ground truth.

    Without `shown`, a later click cannot be told apart from a book the reader
    found some other way, so there is no way to learn what a good
    recommendation was. That is precisely the gap F-13's circular target grew
    to fill.
    """
    try:
        with SessionLocal() as session:
            session.add(
                RecommendationLog(
                    user_id=user_id,
                    request=_trim(request),
                    shown=shown,
                    model_version=model_version,
                    latency_ms=latency_ms,
                )
            )
            session.commit()
        return True
    except Exception as exc:
        log.warning(f"recommendation log failed: {type(exc).__name__}: {exc}")
        return False
