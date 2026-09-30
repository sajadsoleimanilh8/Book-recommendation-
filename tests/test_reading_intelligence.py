"""Reading Intelligence — section 39, Phase 5.

Nothing here is a new client contract: `reading_stats` derives everything
from `interaction_events` rows of type `READING_PAGE`, which already fire
on every genuine page turn. So most of these tests build a timestamp
sequence and check what falls out of it, the same way `test_search.py`
builds a small corpus rather than trusting production data to exercise a
code path.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

from services.reading_intelligence import (  # noqa: E402
    INACTIVITY_NUDGE_DAYS,
    _cluster_sessions,
    _nudge,
    _streak_days,
    reading_stats,
)

UTC = timezone.utc


def _t(day, hour=12, minute=0):
    """A fixed reference month, so tests read as relative offsets."""
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


# --------------------------------------------------------------------------
# _cluster_sessions — no database needed
# --------------------------------------------------------------------------


def test_close_together_turns_are_one_session():
    stamps = [_t(1, 12, 0), _t(1, 12, 5), _t(1, 12, 9)]
    sessions = _cluster_sessions(stamps, gap_minutes=20)
    assert len(sessions) == 1
    assert sessions[0].pages_turned == 3


def test_a_long_gap_splits_into_two_sessions():
    stamps = [_t(1, 12, 0), _t(1, 12, 5), _t(1, 13, 0)]  # 55-minute gap
    sessions = _cluster_sessions(stamps, gap_minutes=20)
    assert len(sessions) == 2
    assert [s.pages_turned for s in sessions] == [2, 1]


def test_a_gap_exactly_at_the_boundary_does_not_split():
    stamps = [_t(1, 12, 0), _t(1, 12, 20)]
    sessions = _cluster_sessions(stamps, gap_minutes=20)
    assert len(sessions) == 1, "a gap of exactly the threshold should not count as exceeding it"


def test_no_timestamps_means_no_sessions():
    assert _cluster_sessions([]) == []


def test_session_duration_is_start_to_end_not_a_count():
    stamps = [_t(1, 12, 0), _t(1, 12, 3), _t(1, 12, 10)]
    sessions = _cluster_sessions(stamps, gap_minutes=20)
    assert sessions[0].duration_minutes == pytest.approx(10.0)


# --------------------------------------------------------------------------
# _streak_days
# --------------------------------------------------------------------------


def test_reading_every_day_in_a_row_is_a_full_streak():
    stamps = [_t(1), _t(2), _t(3)]
    streak, since = _streak_days(stamps, now=_t(3, 23))
    assert streak == 3
    assert since == 0


def test_a_skipped_day_breaks_the_streak():
    stamps = [_t(1), _t(3)]  # day 2 is missing
    streak, since = _streak_days(stamps, now=_t(3, 23))
    assert streak == 1, "only the 3rd counts once the 2nd is missing"


def test_re_reading_the_same_day_twice_counts_once():
    stamps = [_t(1, 9), _t(1, 21)]
    streak, _ = _streak_days(stamps, now=_t(1, 23))
    assert streak == 1


def test_days_since_last_read_counts_forward_from_the_last_turn():
    stamps = [_t(1)]
    _, since = _streak_days(stamps, now=_t(5))
    assert since == 4


def test_no_history_has_no_streak_and_no_days_since():
    streak, since = _streak_days([], now=_t(5))
    assert streak == 0
    assert since is None


# --------------------------------------------------------------------------
# _nudge
# --------------------------------------------------------------------------


def test_long_inactivity_produces_the_recap_nudge():
    text = _nudge(
        book_title="Moby-Dick", days_since_last_read=INACTIVITY_NUDGE_DAYS,
        typical_session_minutes=None, current_page=None,
    )
    assert text is not None
    assert "Moby-Dick" in text and "recap" in text.lower()
    assert str(INACTIVITY_NUDGE_DAYS) in text


def test_a_short_absence_does_not_trigger_the_recap_nudge():
    text = _nudge(
        book_title="Moby-Dick", days_since_last_read=INACTIVITY_NUDGE_DAYS - 1,
        typical_session_minutes=30, current_page=12,
    )
    assert text is None or "recap" not in text.lower()


def test_a_typical_session_length_produces_a_continue_nudge_when_not_inactive():
    text = _nudge(
        book_title="Moby-Dick", days_since_last_read=0,
        typical_session_minutes=25, current_page=42,
    )
    assert text is not None
    assert "25 minutes" in text
    assert "page 42" in text


def test_inactivity_takes_priority_over_a_typical_session_nudge():
    """Telling a reader who has not opened a book in a week "you normally
    read for 25 minutes" would be a strange thing to say; the recap nudge
    is the honest one when both are technically true."""
    text = _nudge(
        book_title="Moby-Dick", days_since_last_read=10,
        typical_session_minutes=25, current_page=42,
    )
    assert "recap" in text.lower()
    assert "25 minutes" not in text


def test_no_history_at_all_produces_no_nudge():
    """The absence is the honest answer — a fabricated nudge for a reader
    with no real pattern would be exactly the "plausible-looking guess"
    this project avoids elsewhere."""
    text = _nudge(
        book_title="Moby-Dick", days_since_last_read=None,
        typical_session_minutes=None, current_page=None,
    )
    assert text is None


def test_no_book_title_produces_no_recap_nudge():
    text = _nudge(
        book_title=None, days_since_last_read=30,
        typical_session_minutes=None, current_page=None,
    )
    assert text is None


# --------------------------------------------------------------------------
# reading_stats — the real storage path
# --------------------------------------------------------------------------


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
class TestReadingStats:
    @pytest.fixture(scope="class")
    def book_id(self):
        from db import SessionLocal
        from models import Book
        from sqlalchemy import select

        with SessionLocal() as session:
            return session.scalar(select(Book.id).order_by(Book.id).limit(1))

    @pytest.fixture
    def reader(self):
        import uuid

        from db import SessionLocal
        from models import User

        tag = uuid.uuid4().hex[:12]
        with SessionLocal() as session:
            user = User(email=f"reading-intel-{tag}@example.com", password_hash="x")
            session.add(user)
            session.commit()
            user_id = user.id

        yield user_id

        with SessionLocal() as session:
            from models import InteractionEvent

            # `interaction_events.user_id` is ON DELETE SET NULL, not
            # CASCADE (anonymous rows are still signal) — deleting the user
            # alone would leave this test's rows behind as orphaned debris.
            session.execute(
                InteractionEvent.__table__.delete().where(InteractionEvent.user_id == user_id)
            )
            u = session.get(User, user_id)
            if u is not None:
                session.delete(u)  # reading_progress cascades
            session.commit()

    def _turn(self, book_id, page, created_at, *, page_size=1, had_content=True, excerpt_pages=20):
        return {
            "book_id": book_id,
            "event_type": "reading_page",
            "context": {
                "page": page, "page_size": page_size,
                "excerpt_pages": excerpt_pages, "had_content": had_content,
            },
            "created_at": created_at,
        }

    def _insert(self, session, user_id, turns):
        from models import InteractionEvent

        session.add_all(
            InteractionEvent(user_id=user_id, **turn) for turn in turns
        )
        session.commit()

    def test_no_history_is_answered_honestly(self, reader, book_id):
        from db import SessionLocal

        with SessionLocal() as session:
            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(10))

        assert result["current_page"] is None
        assert result["sessions_considered"] == 0
        assert result["streak_days"] == 0
        assert result["nudge"] is None

    def test_two_sessions_are_counted_and_a_continue_nudge_is_produced(self, reader, book_id):
        from db import SessionLocal
        from models import ReadingProgress

        turns = [
            self._turn(book_id, 1, _t(1, 20, 0)),
            self._turn(book_id, 2, _t(1, 20, 10)),
            self._turn(book_id, 3, _t(1, 20, 25)),
            self._turn(book_id, 4, _t(2, 20, 0)),
            self._turn(book_id, 5, _t(2, 20, 20)),
        ]
        with SessionLocal() as session:
            self._insert(session, reader, turns)
            session.add(ReadingProgress(user_id=reader, book_id=book_id, progress=0.4, page=5))
            session.commit()

            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(2, 21))

        assert result["current_page"] == 5
        assert result["percent"] == 40
        assert result["sessions_considered"] == 2
        assert result["streak_days"] == 2
        assert result["days_since_last_read"] == 0
        assert result["typical_session_minutes"] is not None
        assert result["nudge"] is not None
        assert "page 5" in result["nudge"]

    def test_a_bulk_fetch_is_not_counted_as_a_page_turn(self, reader, book_id):
        """The same discipline `services/reading_depth.py::aggregate` uses:
        only `page_size == 1` is a genuine turn. A page_size=24 fetch (the
        default listing call) must not manufacture a reading session."""
        from db import SessionLocal

        turns = [self._turn(book_id, 1, _t(1, 9, 0), page_size=24)]
        with SessionLocal() as session:
            self._insert(session, reader, turns)
            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(1, 10))

        assert result["sessions_considered"] == 0

    def test_a_page_with_no_content_is_not_counted(self, reader, book_id):
        from db import SessionLocal

        turns = [self._turn(book_id, 999, _t(1, 9, 0), had_content=False)]
        with SessionLocal() as session:
            self._insert(session, reader, turns)
            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(1, 10))

        assert result["sessions_considered"] == 0

    def test_long_inactivity_is_reported_honestly(self, reader, book_id):
        from db import SessionLocal

        turns = [self._turn(book_id, 1, _t(1, 9, 0)), self._turn(book_id, 2, _t(1, 9, 5))]
        with SessionLocal() as session:
            self._insert(session, reader, turns)
            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(10, 9))

        assert result["days_since_last_read"] == 9
        assert result["nudge"] is not None
        assert "recap" in result["nudge"].lower()

    def test_another_readers_turns_never_leak_into_this_readers_stats(self, reader, book_id):
        """The authorization property that matters most here: this is a
        per-reader read, and it must query only this reader's own rows."""
        import uuid

        from db import SessionLocal
        from models import InteractionEvent, User

        with SessionLocal() as session:
            other = User(email=f"reading-intel-other-{uuid.uuid4().hex[:12]}@example.com", password_hash="x")
            session.add(other)
            session.commit()
            other_id = other.id

            self._insert(session, other_id, [self._turn(book_id, 1, _t(1, 9, 0))])
            result = reading_stats(session, user_id=reader, book_id=book_id, now=_t(1, 10))

            session.execute(
                InteractionEvent.__table__.delete().where(InteractionEvent.user_id == other_id)
            )
            session.delete(session.get(User, other_id))
            session.commit()

        assert result["sessions_considered"] == 0, "another reader's page turns were counted"


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
class TestReadingStatsEndpoint:
    def _client(self, fitted_app):
        return fitted_app[1]

    def _register(self, client, email):
        r = client.post(
            "/api/auth/register", json={"email": email, "password": "correct horse battery staple"}
        )
        assert r.status_code < 400, r.text
        token = r.json()["access_token"]
        user_id = r.json()["user"]["id"]
        return {"Authorization": f"Bearer {token}"}, user_id

    def _book_id(self):
        from db import SessionLocal
        from models import Book
        from sqlalchemy import select

        with SessionLocal() as session:
            return session.scalar(select(Book.id).order_by(Book.id).limit(1))

    def _delete_user(self, user_id):
        from db import SessionLocal
        from models import User

        with SessionLocal() as session:
            u = session.get(User, user_id)
            if u is not None:
                session.delete(u)
                session.commit()

    def test_the_endpoint_requires_a_signed_in_reader(self, fitted_app):
        client = self._client(fitted_app)
        book_id = self._book_id()
        r = client.get(f"/api/books/{book_id}/reading-stats")
        assert r.status_code in (401, 403)

    def test_a_signed_in_reader_with_no_history_gets_an_honest_empty_answer(self, fitted_app):
        import uuid

        client = self._client(fitted_app)
        headers, user_id = self._register(client, f"reading-intel-ep-{uuid.uuid4().hex[:12]}@example.com")
        book_id = self._book_id()

        try:
            r = client.get(f"/api/books/{book_id}/reading-stats", headers=headers)

            assert r.status_code == 200, r.text
            body = r.json()
            assert body["book_id"] == book_id
            assert body["sessions_considered"] == 0
            assert body["nudge"] is None
        finally:
            self._delete_user(user_id)
