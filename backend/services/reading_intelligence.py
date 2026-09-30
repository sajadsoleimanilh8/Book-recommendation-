"""Reading Intelligence — prmpt.md section 39, Phase 5.

Section 39 lists a broad tracking wish list (current book, chapter, page,
session, duration, speed, streak, completion estimate) plus two example
nudges:

    "You normally read for 25 minutes in the evening. Continue Chapter 7?"
    "You haven't opened this book in six days. Want a three-minute recap?"

**What is built here, and why the list above is shorter than section 39's.**
Current page and percent already exist (`reading_progress`, section 12/29).
Everything else in this module is derived, not newly collected — from
`interaction_events` rows of type `READING_PAGE`, which already fire once
per genuine page turn (`api/books.py::_record_page_turn`) and are already
attributed to a signed-in reader (OI-6). No new client contract, no new
table.

Two things section 39's own examples imply that this module deliberately
does **not** claim:

- **Time of day.** The first example says "in the evening", which would
  need each reader's own timezone to say honestly — `users` carries none.
  Guessing at it from a server-side UTC timestamp would misdescribe a
  reader in a different timezone as evening-reading when they read at
  lunch, which is a wrong claim stated with confidence, not an honest
  approximation. Left out; the nudge below states a typical *duration*
  instead, which needs no timezone to be true.
- **Completion estimate.** Reading speed here would need a reliable total
  page count, and the only one on record is `progress.page /
  progress.progress`, a division recovered from a client-reported fraction
  that is unreliable exactly when it matters most (early in a book, small
  denominators magnify rounding badly). A number produced by dividing one
  approximation by another and presenting it as "time left" is the same
  failure mode section 26 (Explainable Recommendations) was left unbuilt to
  avoid: a plausible answer built on data that is not actually there yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Optional

from sqlalchemy import select

import events as events_module
from models import Book, InteractionEvent, ReadingProgress

# Two page turns more than this far apart are treated as separate sittings
# rather than one long one. Twenty minutes is long enough that a reader
# putting the book down to answer the door does not fracture one session
# into several, short enough that an evening and the next morning do not
# merge into one.
SESSION_GAP_MINUTES = 20

# How many of the most recent sessions set the "typical" duration reported
# back to a reader. Bounded for the same reason `RECENT_MEMORY_LIMIT` is in
# `services/memory.py`: a habit from a year ago should not outweigh how
# someone reads today.
RECENT_SESSIONS_FOR_TYPICAL = 5

# Below this many days since the last page turn, "haven't opened it in N
# days" is not yet a nudge worth sending — everyone pauses a book for a day
# or two.
INACTIVITY_NUDGE_DAYS = 3


@dataclass(frozen=True)
class ReadingSitting:
    started_at: datetime
    ended_at: datetime
    pages_turned: int

    @property
    def duration_minutes(self) -> float:
        return (self.ended_at - self.started_at).total_seconds() / 60.0


def _cluster_sessions(
    timestamps: list[datetime], *, gap_minutes: int = SESSION_GAP_MINUTES
) -> list[ReadingSitting]:
    """Group ascending timestamps into sittings, splitting wherever the
    reader was away for more than `gap_minutes`.

    A heuristic, not a fact reported to the reader directly — the boundary
    it draws is an engineering choice (same category as `search.MIN_SIMILARITY`
    or `copilot.RETRIEVAL_POOL`), not a claim about what the reader actually
    did between two page turns.
    """
    if not timestamps:
        return []
    gap = timedelta(minutes=gap_minutes)
    groups: list[list[datetime]] = [[timestamps[0]]]
    for ts in timestamps[1:]:
        if ts - groups[-1][-1] > gap:
            groups.append([ts])
        else:
            groups[-1].append(ts)
    return [
        ReadingSitting(started_at=g[0], ended_at=g[-1], pages_turned=len(g)) for g in groups
    ]


def _streak_days(timestamps: list[datetime], *, now: datetime) -> tuple[int, Optional[int]]:
    """(consecutive days read up to today, days since the last page turn).

    Calendar days in UTC — the same approximation `created_at` itself is
    stored in, and the same caveat as the nudge's missing time-of-day: a
    reader near a day boundary in another timezone may see an off-by-one
    streak. Bounded imprecision, not a fabricated claim, which is the line
    this module tries to hold throughout.
    """
    if not timestamps:
        return 0, None

    days = sorted({ts.astimezone(timezone.utc).date() for ts in timestamps}, reverse=True)
    today = now.astimezone(timezone.utc).date()
    days_since_last_read = (today - days[0]).days

    streak = 0
    expected = days[0]
    for day in days:
        if day == expected:
            streak += 1
            expected = expected - timedelta(days=1)
        else:
            break
    return streak, days_since_last_read


def _nudge(
    *, book_title: Optional[str], days_since_last_read: Optional[int],
    typical_session_minutes: Optional[float], current_page: Optional[int],
) -> Optional[str]:
    """One of section 39's two example shapes, or nothing.

    An absent nudge is the honest answer when there is no real pattern yet
    (a first session, or too little history) — the same "return nothing
    rather than a plausible-looking guess" rule the rest of this project
    already follows for retrieval and grounding.
    """
    if book_title and days_since_last_read is not None and days_since_last_read >= INACTIVITY_NUDGE_DAYS:
        return f'You haven\'t opened "{book_title}" in {days_since_last_read} days. Want a quick recap?'
    if typical_session_minutes and current_page:
        minutes = round(typical_session_minutes)
        if minutes >= 1:
            return f"You normally read for about {minutes} minutes at a time. Continue on page {current_page}?"
    return None


def reading_stats(
    session, *, user_id: int, book_id: int, now: Optional[datetime] = None
) -> dict[str, Any]:
    """Section 39's per-book tracking, derived from what is already stored.

    `now` is injectable, the same reason `llm` is injected elsewhere in
    Phase 5 — streaks and "days since" are wall-clock-relative and
    untestable against a moving `datetime.now()`.
    """
    now = now or datetime.now(timezone.utc)

    progress_row = session.get(ReadingProgress, (user_id, book_id))
    book = session.get(Book, book_id)

    rows = session.execute(
        select(InteractionEvent.created_at, InteractionEvent.context)
        .where(
            InteractionEvent.user_id == user_id,
            InteractionEvent.book_id == book_id,
            InteractionEvent.event_type == events_module.READING_PAGE,
        )
        .order_by(InteractionEvent.created_at)
    ).all()

    # Same filter `services/reading_depth.py::aggregate` applies: only a
    # genuine single-page turn that actually returned text is evidence of
    # reading. A bulk fetch or a request past the end of the excerpt is not.
    timestamps = [
        created_at
        for created_at, context in rows
        if context and context.get("page_size") == 1 and context.get("had_content")
    ]

    sessions = _cluster_sessions(timestamps)
    streak_days, days_since_last_read = _streak_days(timestamps, now=now)

    multi_turn = [s for s in sessions if s.pages_turned >= 2]
    recent = multi_turn[-RECENT_SESSIONS_FOR_TYPICAL:]
    typical_session_minutes = (
        median(s.duration_minutes for s in recent) if recent else None
    )
    last_session_minutes = multi_turn[-1].duration_minutes if multi_turn else None

    current_page = progress_row.page if progress_row else None
    percent = int(progress_row.progress * 100) if progress_row else None

    return {
        "book_id": book_id,
        "current_page": current_page,
        "percent": percent,
        "sessions_considered": len(sessions),
        "streak_days": streak_days,
        "days_since_last_read": days_since_last_read,
        "last_session_minutes": (
            round(last_session_minutes, 1) if last_session_minutes is not None else None
        ),
        "typical_session_minutes": (
            round(typical_session_minutes, 1) if typical_session_minutes is not None else None
        ),
        "nudge": _nudge(
            book_title=book.title if book else None,
            days_since_last_read=days_since_last_read,
            typical_session_minutes=typical_session_minutes,
            current_page=current_page,
        ),
    }
