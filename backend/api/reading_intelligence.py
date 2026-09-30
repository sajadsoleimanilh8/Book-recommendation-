"""Reading Intelligence — GET /api/books/{book_id}/reading-stats — section 39.

Thin, like the rest of Phase 5's endpoints: `services.reading_intelligence`
owns the derivation, this just resolves the caller and hands off.

**`book_id` here is the real `books.id` primary key** — the same space
`api/copilot.py`'s `/ask` and `/summary` already use, and what
`interaction_events.book_id` and `reading_progress.book_id` are foreign keys
to. This is deliberately **not** the positional catalogue id
`api/reading.py`'s `/api/progress` and `/api/reminder` accept (a DataFrame
row index + 1, translated to the real id internally via
`store.resolve_book_pk` before anything is stored) — this module reads
those same tables back, so it has to use the id they are actually keyed by,
not the one their own request bodies happen to take.

`CurrentUser`, not `OptionalUser`: every number this endpoint returns is
this reader's own history. An anonymous caller has no persisted history to
attribute back to them (`interaction_events.user_id` is `NULL` for them
regardless of which book they mean), so there is nothing honest to answer.
"""

from __future__ import annotations

from fastapi import APIRouter

from auth import CurrentUser, SessionDep
from services.reading_intelligence import reading_stats

router = APIRouter()


@router.get("/api/books/{book_id}/reading-stats")
def reading_stats_route(book_id: int, user: CurrentUser, session: SessionDep):
    """This reader's own session/streak/nudge picture for one book."""
    return reading_stats(session, user_id=user.id, book_id=book_id)
