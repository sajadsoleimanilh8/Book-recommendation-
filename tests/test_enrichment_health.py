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


# -- F-53's residual enhancement: run history readable from the app --------
#
# `/health`'s `enrichment.{pending, last_progress, stalled}` deliberately
# stays derived from `max(books.enriched_at)`, not from the log — F-53 is
# exactly the case where the log said a run succeeded and progress still
# did not happen, so the log can never become a second source of truth
# about whether enrichment is working. This answers a different question:
# what has the standing job actually reported, run by run, without needing
# a terminal on whichever machine happens to be running it.

SAMPLE_LOG = """\
2026-09-17 14:50:34  exit=0
2026-09-17 14:50:34        ok                     2
2026-09-17 14:50:34        processed              2
2026-09-17 14:50:34        quota_exhausted        False
2026-09-17 14:50:34        throttled              True
2026-09-17 14:50:34        description_coverage   23.43%
2026-09-17 14:50:34        english_still_pending  12669
2026-09-17 22:35:45  exit=0
2026-09-17 22:35:45        processed              0
2026-09-17 22:35:45        quota_exhausted        False
2026-09-17 22:35:45        throttled              True
2026-09-17 22:35:45        description_coverage   23.43%
2026-09-17 22:35:45        english_still_pending  12669
"""


def test_the_parser_reads_every_run_and_its_own_keys():
    """Keys vary run to run (a barren run logs fewer than one with real
    progress) — the parser must not assume a fixed schema."""
    runs = health._parse_enrichment_history(SAMPLE_LOG)

    assert len(runs) == 2
    assert runs[0]["timestamp"] == "2026-09-17 14:50:34"
    assert runs[0]["exit_code"] == 0
    assert runs[0]["ok"] == "2"
    assert runs[0]["english_still_pending"] == "12669"
    assert "ok" not in runs[1], "the barren run never logged this key"
    assert runs[1]["processed"] == "0"


def test_the_parser_reads_the_real_log_without_crashing():
    """Contract with the real, uncontrolled file (`run_daily_enrichment.ps1`
    writes it, this backend does not) — parsed here to prove the format
    assumption holds, not to assert on its content, which changes daily."""
    if not health.ENRICHMENT_HISTORY_LOG.exists():
        pytest.skip("no history.log on this instance")
    text = health.ENRICHMENT_HISTORY_LOG.read_text(encoding="utf-8", errors="replace")
    runs = health._parse_enrichment_history(text)
    assert isinstance(runs, list)
    if runs:
        assert "timestamp" in runs[0] and "exit_code" in runs[0]


def test_an_unparseable_line_is_skipped_not_fatal():
    """F-21's rule applies here too: a monitoring detail must not 500 the
    endpoint that reports it.

    Two placements matter, not one: garbage *before* the first run header
    is trivially ignored by the `current is not None` guard regardless of
    whether `_METRIC_LINE` itself is strict — that alone would pass even
    with a regex that accepts anything. Garbage *after* a valid header is
    the real test of the regex, because a too-permissive pattern would
    silently add a bogus key to that run's dict rather than being rejected,
    and a bare run count would not show that.
    """
    garbled = "this is not a log line at all\n" + SAMPLE_LOG + "neither is this\n"
    runs = health._parse_enrichment_history(garbled)

    assert len(runs) == 2
    assert "neither" not in runs[-1], (
        "a garbage line after a valid header was accepted as a real metric"
    )
    assert runs[-1] == {
        "timestamp": "2026-09-17 22:35:45",
        "exit_code": 0,
        "processed": "0",
        "quota_exhausted": "False",
        "throttled": "True",
        "description_coverage": "23.43%",
        "english_still_pending": "12669",
    }, "the trailing garbage line changed the last run's own, valid keys"


def test_empty_log_is_an_empty_list_not_an_error():
    assert health._parse_enrichment_history("") == []


@pytest.fixture
def history_log(tmp_path, monkeypatch):
    log_path = tmp_path / "history.log"
    monkeypatch.setattr(health, "ENRICHMENT_HISTORY_LOG", log_path)
    return log_path


def test_endpoint_reports_unavailable_when_the_log_does_not_exist(history_log):
    """Honest about a fresh install or a machine that never ran the
    scheduled job — not an error, an absence (F-17's shape)."""
    result = health.enrichment_history()
    assert result["ok"] is True
    assert result["available"] is False
    assert result["runs"] == []


def test_endpoint_returns_real_runs_from_the_log(history_log):
    history_log.write_text(SAMPLE_LOG, encoding="utf-8")
    result = health.enrichment_history()

    assert result["available"] is True
    assert result["total_runs_logged"] == 2
    assert result["returned"] == 2
    assert result["runs"][-1]["timestamp"] == "2026-09-17 22:35:45"


def test_endpoint_bounds_the_returned_runs_but_not_the_reported_total(history_log):
    """A long-running instance's log grows forever; the endpoint must
    answer in constant time regardless, while still saying honestly how
    much history actually exists."""
    many_runs = SAMPLE_LOG * (health.MAX_HISTORY_RUNS + 5)
    history_log.write_text(many_runs, encoding="utf-8")
    result = health.enrichment_history()

    assert result["total_runs_logged"] == 2 * (health.MAX_HISTORY_RUNS + 5)
    assert result["returned"] == health.MAX_HISTORY_RUNS
    assert len(result["runs"]) == health.MAX_HISTORY_RUNS
