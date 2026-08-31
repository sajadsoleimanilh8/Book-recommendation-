"""F-42 — interaction logging, the seed of the data moat (§54).

`interaction_events` and `recommendation_log` shipped in PR 2 with indexes and
docstrings insisting they must fill before model work, because history cannot
be backfilled. Nothing ever wrote to them. Both were empty.

These tests hold three properties that are far cheaper to build in than to
retrofit: logging never breaks the request, a failed log never poisons the
caller's transaction, and anonymous traffic is still recorded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import events  # noqa: E402
from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)


@pytest.fixture
def clean_events():
    from db import SessionLocal
    from models import InteractionEvent, RecommendationLog

    def _clear():
        with SessionLocal() as s:
            s.query(InteractionEvent).delete()
            s.query(RecommendationLog).delete()
            s.commit()

    _clear()
    yield
    _clear()


def _events(event_type=None):
    from db import SessionLocal
    from models import InteractionEvent

    with SessionLocal() as s:
        q = s.query(InteractionEvent)
        if event_type:
            q = q.filter(InteractionEvent.event_type == event_type)
        return q.all()


# --- the module itself ----------------------------------------------------


def test_an_event_is_written(clean_events):
    assert events.record(events.SEARCH, context={"q": "whales", "results": 3})
    rows = _events(events.SEARCH)
    assert len(rows) == 1
    assert rows[0].context["q"] == "whales"


def test_anonymous_events_are_kept(clean_events):
    """user_id is nullable on purpose. Dropping anonymous traffic would throw
    away most of what a young product sees."""
    assert events.record(events.BOOK_VIEW, user_id=None, book_id=None)
    assert _events(events.BOOK_VIEW)[0].user_id is None


def test_an_unknown_event_type_is_refused(clean_events):
    """A typo'd event type is a silent hole in the data six months later."""
    assert events.record("viewed") is False
    assert _events() == []


def test_a_broken_database_does_not_raise(monkeypatch):
    """Logging must never break the request. A recommendation that 500s
    because analytics failed is strictly worse than one nobody measured."""

    def explode():
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(events, "SessionLocal", explode)
    assert events.record(events.SEARCH, context={"q": "x"}) is False
    assert events.record_recommendation(request={"a": 1}) is False


def test_long_context_strings_are_trimmed(clean_events):
    """`context` is JSONB and tempting to overfill."""
    events.record(events.SEARCH, context={"q": "x" * 5000})
    assert len(_events(events.SEARCH)[0].context["q"]) <= 400


def test_a_recommendation_records_what_was_shown(clean_events):
    """A click means nothing without the books that were offered and passed
    over — they are half the training signal and exist nowhere else."""
    from db import SessionLocal
    from models import RecommendationLog

    assert events.record_recommendation(
        request={"genre": "Fiction"},
        shown={"book_ids": [1, 2, 3], "ranks": [1, 2, 3]},
        model_version="rec-test",
        latency_ms=42,
    )
    with SessionLocal() as s:
        row = s.query(RecommendationLog).one()
    assert row.shown["book_ids"] == [1, 2, 3]
    assert row.model_version == "rec-test"
    assert row.latency_ms == 42


# --- wired into the endpoints --------------------------------------------


@pytest.fixture
def client(fitted_app):
    _, c = fitted_app
    return c


def test_a_book_view_is_recorded(client, clean_events):
    assert client.get("/api/books/1").status_code == 200
    assert len(_events(events.BOOK_VIEW)) == 1


def test_a_search_records_its_query_and_result_count(client, clean_events):
    response = client.get("/api/search/semantic", params={"q": "haunted house"})
    if response.status_code != 200:
        pytest.skip("semantic search unavailable in this environment")
    rows = _events(events.SEARCH)
    assert len(rows) == 1
    assert rows[0].context["q"] == "haunted house"
    assert rows[0].context["results"] == response.json()["total"]


def test_a_recommendation_is_logged_with_latency(client, clean_events):
    from db import SessionLocal
    from models import RecommendationLog

    assert client.post("/api/recommend", json={"top_k": 5}).status_code == 200
    with SessionLocal() as s:
        row = s.query(RecommendationLog).one()
    assert row.latency_ms is not None and row.latency_ms >= 0
    assert row.model_version
    assert len(row.shown["book_ids"]) <= 5


def test_a_failing_logger_does_not_break_the_endpoint(client, clean_events, monkeypatch):
    """The property that matters most. If this ever regresses, an analytics
    outage becomes a product outage."""

    def explode(*a, **k):
        raise RuntimeError("logger exploded")

    # Patch below the swallow, so the failure is real rather than simulated.
    monkeypatch.setattr(events, "SessionLocal", explode)

    assert client.get("/api/books/1").status_code == 200
    assert client.post("/api/recommend", json={"top_k": 3}).status_code == 200
