"""F-53 — a job that runs green while doing nothing.

The standing enrichment job ran 2026-09-17 to 2026-09-20 logging
`processed 0, throttled True`, exit code 0, Task Scheduler "Last Result: 0".
Every component behaved correctly: Google answered 403, the F-29 circuit
breaker stopped cleanly after three throttles, rows were left `pending`
rather than falsely `not_found`. Nothing was corrupted and no component was
wrong. The *outcome* was 72 hours of zero progress that looked identical to
success from every angle a human would check.

These tests pin the property that matters: **progress, not liveness.** A
check that the job ran would have been green throughout the incident.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytest.importorskip("sqlalchemy", reason="persistence stack not installed")

import main  # noqa: E402,F401  (imported first — see test_language_filter.py)
from api import health  # noqa: E402


class _FakeSession:
    """Stands in for the one query `_enrichment_health` makes."""

    def __init__(self, result):
        self._result = result

    def execute(self, *_a, **_k):
        class _R:
            def __init__(self, r):
                self._r = r

            def one(self):
                if isinstance(self._r, Exception):
                    raise self._r
                return self._r

        return _R(self._result)

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


@pytest.fixture
def db(monkeypatch):
    def _set(pending, last):
        monkeypatch.setattr(
            health, "SessionLocal", lambda: _FakeSession((pending, last))
        )

    return _set


def _days_ago(n: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=n)


# -- the incident ----------------------------------------------------------


def test_three_days_of_no_progress_with_work_remaining_is_a_warning(db):
    """The exact shape of F-53."""
    db(21_274, _days_ago(3.2))
    result = health._enrichment_health()

    assert result["stalled"] is True
    assert result["warning"] and "no progress" in result["warning"]
    assert "21274" in result["warning"], "the warning does not say how much is stuck"


def test_a_job_that_is_merely_slow_is_not_a_warning(db):
    """Yesterday's run enriched 992 books. Warning on that would train
    everyone to ignore this field, which is how the next F-53 goes unseen."""
    db(21_274, _days_ago(1.03))
    result = health._enrichment_health()

    assert result["stalled"] is False and result["warning"] is None


@pytest.mark.parametrize("days,expected", [(2.9, False), (3.0, True), (7.0, True)])
def test_the_threshold_is_where_it_is_documented(db, days, expected):
    db(100, _days_ago(days))
    assert health._enrichment_health()["stalled"] is expected


# -- the states that must NOT warn ----------------------------------------


def test_a_drained_backlog_is_not_a_stall(db):
    """Once there is nothing left to enrich, a long quiet period is the job
    having finished. This is the half that keeps the warning meaningful."""
    db(0, _days_ago(90))
    result = health._enrichment_health()

    assert result["stalled"] is False, "warned about a job that has nothing left to do"


def test_a_fresh_install_reports_an_empty_state_rather_than_an_alarm(db):
    """No book has ever been enriched. That is indistinguishable from here
    from 'the job was never set up', and F-17's rule is to report the honest
    empty state rather than invent a reading."""
    db(21_274, None)
    result = health._enrichment_health()

    assert result["last_progress"] is None
    assert result["stalled"] is False


def test_a_database_failure_degrades_instead_of_500ing_health(monkeypatch):
    """/health returning 500 is F-21. A monitoring endpoint that fails when
    the thing it monitors is unhealthy is worse than not having one."""
    monkeypatch.setattr(
        health, "SessionLocal", lambda: _FakeSession(RuntimeError("db is gone"))
    )
    result = health._enrichment_health()

    assert result["error"] == "unavailable"
    assert result["pending"] is None


# -- it has to be wired in ------------------------------------------------


def test_health_actually_reports_it(fitted_app):
    """A correct probe the endpoint never calls warns nobody — the same
    mistake as `production_blockers`, which sat in `config.summary()` while
    `/health` did not call it."""
    _, client = fitted_app
    body = client.get("/api/health").json()

    assert "enrichment" in body, "/health does not report enrichment progress"
    assert "pending" in body["enrichment"]


def test_it_measures_progress_not_liveness():
    """The property this file exists for, asserted against the source.

    A run-outcome table would record what the job *says* it did, and F-53 is
    the case where the job said it succeeded. `max(enriched_at)` is the last
    moment any book's enrichment state actually changed, so it cannot report
    progress that did not happen.
    """
    src = (BACKEND / "api" / "health.py").read_text(encoding="utf-8")
    body = src.split("def _enrichment_health", 1)[1]
    code = "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))

    assert "enriched_at" in code, (
        "the stall check no longer reads when enrichment last changed anything"
    )


def test_uploads_do_not_count_towards_the_backlog():
    """Phase 4: a private upload is not catalogue work, and the enrichment
    passes exclude it (F-57). If it counted here, a reader uploading books
    would inflate `pending` and could hold the warning on forever."""
    src = (BACKEND / "api" / "health.py").read_text(encoding="utf-8")
    body = src.split("def _enrichment_health", 1)[1]

    assert "owner_id" in body, "the pending count includes private uploads"
