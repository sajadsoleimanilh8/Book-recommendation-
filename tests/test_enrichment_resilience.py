"""Why the nightly enrichment pass stopped doing anything — F-65.

Every run from 2026-09-25 onward logged `processed 0, throttled True,
quota_exhausted False` and exited 0, so Task Scheduler reported success for
five days while coverage sat frozen. Diagnosed by elimination: the key was
valid, the quota untouched, DNS clean, no proxy configured, the host fine,
the user agent irrelevant, and the job's own code path worked when run by
hand. What was left was the schedule and the breaker.

Two faults, and they compounded:

  * The task fires **on wake**, not at 04:00, so its first requests go out
    over a network that has not finished coming up.
  * A 403 is raised *without* retries — correctly, since retrying a real 403
    spends quota to learn nothing — so three of them arrive back to back in
    about six seconds. Three strikes tripped the breaker. The pass ended
    before its first book, and reported "throttled", which pointed the
    investigation at the provider rather than the clock.

So the breaker was measuring the wrong thing: how many refusals, not whether
they were sustained. These tests pin the shape of both halves. They use a
fake clock and a fake sleeper — the point is the decision, not the waiting.
"""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import scripts.enrich as enrich_mod  # noqa: E402
from providers.base import (  # noqa: E402
    ProviderThrottled,
    ProviderUnreachable,
    describe_http_error,
)

try:
    from conftest import database_reachable

    HAVE_DB = database_reachable()
except Exception:  # pragma: no cover - conftest is always importable under pytest
    HAVE_DB = False

needs_db = pytest.mark.skipif(not HAVE_DB, reason="database unavailable")


# -- (c) a refusal, written down in full ----------------------------------


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, code, reason, headers, body):
        self._body = body.encode()
        super().__init__("https://example.invalid/x", code, reason, headers, None)

    def read(self, *a):
        return self._body


GOOGLE_403_PAGE = (
    "<!DOCTYPE html>\n<html lang=en>\n  <meta charset=utf-8>\n"
    "  <meta name=viewport content=\"initial-scale=1, minimum-scale=1\">\n"
    "  <title>Error 403 (Forbidden)!!1</title>\n"
    "  <style>*{margin:0;padding:0}html,code{font:15px/22px arial}</style>\n"
    "  <p>Your client does not have permission to get URL <code>/books/v1/"
    "volumes</code> from this server.</p>\n"
)


def test_the_error_body_is_kept_long_enough_to_read():
    """The old message cut the body at 120 characters. On the page above that
    is the doctype, the charset and half a meta tag — it never reached the
    part that says what went wrong, which is why five days of logs could not
    tell an expired key from something standing in the way.
    """
    exc = _FakeHTTPError(403, "Forbidden", {}, GOOGLE_403_PAGE)
    detail = describe_http_error(exc, GOOGLE_403_PAGE)

    assert "Error 403 (Forbidden)" in detail, "the <title> is the one-line answer"
    assert "does not have permission" in detail, "the body's actual sentence"
    assert len(detail) > 120


def test_the_headers_that_identify_the_refuser_are_logged():
    """`Via` means something is proxying; `X-Debug-Tracking-Id` is Google's
    own request id and the thing to quote in a report. Neither was recorded,
    so neither could be checked after the fact."""
    exc = _FakeHTTPError(
        403,
        "Forbidden",
        {
            "Via": "1.1 corp-proxy (squid/5.7)",
            "X-Debug-Tracking-Id": "1234567890123456789",
            "Content-Type": "text/html; charset=UTF-8",
        },
        GOOGLE_403_PAGE,
    )
    detail = describe_http_error(exc, GOOGLE_403_PAGE)

    assert "corp-proxy" in detail
    assert "1234567890123456789" in detail
    assert "text/html" in detail


def test_a_body_free_refusal_still_describes_itself():
    exc = _FakeHTTPError(503, "Service Unavailable", {"Retry-After": "120"}, "")
    detail = describe_http_error(exc, "")
    assert "503" in detail and "Retry-After" in detail


# -- (a) the preflight ----------------------------------------------------


class _Probe:
    """A scripted `probe`: each call pops the next outcome."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return None


class _Clock:
    """A fake clock that only moves when something sleeps."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


@pytest.fixture
def enrich(monkeypatch):
    import scripts.enrich as mod

    return mod


class _Prov:
    def __init__(self, configured=True):
        self.configured = configured

    def probe_url(self):
        return "https://provider.invalid/probe"


def test_a_cold_network_is_waited_out_not_given_up_on(enrich, monkeypatch):
    """The actual F-65 failure. The network is not up when the task fires;
    a minute later it is. The pass should survive that."""
    clock = _Clock()
    probe = _Probe(
        ProviderUnreachable("getaddrinfo failed"),  # openlib, attempt 1
        ProviderUnreachable("getaddrinfo failed"),  # openlib, attempt 2
        None,  # openlib, attempt 3 — network up
        None,  # google
    )
    monkeypatch.setattr(enrich, "probe", probe)

    result = enrich.preflight(_Prov(), _Prov(), sleeper=clock.sleep)

    assert result["reachable"] is True
    assert result["google"] is True
    assert result["waited"] > 0, "it should have waited rather than failing"
    assert len(clock.slept) == 2


def test_a_network_that_never_comes_up_marks_nothing(enrich, monkeypatch):
    """Giving up is allowed; giving up *quietly* is what made this invisible.
    No book may be marked on a pass that reached nothing."""
    clock = _Clock()
    probe = _Probe(*[ProviderUnreachable("down")] * 12)
    monkeypatch.setattr(enrich, "probe", probe)

    result = enrich.preflight(_Prov(), _Prov(), sleeper=clock.sleep)

    assert result["reachable"] is False
    assert result["detail"] and "down" in result["detail"]
    assert len(clock.slept) == len(enrich.PREFLIGHT_DELAYS)


def test_only_google_refusing_does_not_stop_the_pass(enrich, monkeypatch):
    """Open Library answers, Google does not. Waiting cannot fix a key, a
    quota or a proxy, so the pass continues on Open Library — which still
    fills ISBNs, years and older descriptions — rather than doing nothing."""
    clock = _Clock()
    probe = _Probe(
        None,  # openlib: fine
        ProviderUnreachable("blocked, not a miss: HTTP 403 Forbidden; Via=..."),
    )
    monkeypatch.setattr(enrich, "probe", probe)

    result = enrich.preflight(_Prov(), _Prov(), sleeper=clock.sleep)

    assert result["reachable"] is True
    assert result["google"] is False, "Google is out, but the pass is not"
    assert clock.slept == [], "a 403 is an answer; there is nothing to wait for"


def test_an_unconfigured_google_is_not_probed(enrich, monkeypatch):
    probe = _Probe(None)
    monkeypatch.setattr(enrich, "probe", probe)

    result = enrich.preflight(_Prov(configured=False), _Prov(), sleeper=lambda s: None)

    assert result["reachable"] is True and result["google"] is False
    assert len(probe.calls) == 1, "only Open Library should have been asked"


# -- (b) the breaker needs time, not just a count -------------------------


@pytest.fixture
def throttling_run(enrich, monkeypatch):
    """Drive `run()` over a fixed number of books with a scripted
    `enrich_one`, a fake clock and no database writes."""
    from sqlalchemy import select

    from models import Book

    def drive(outcomes, books=8):
        clock = _Clock()
        monkeypatch.setattr(enrich.time, "monotonic", clock.monotonic)
        monkeypatch.setattr(
            enrich, "pending_query", lambda *a, **k: select(Book).limit(books)
        )

        seq = list(outcomes)

        def fake_enrich_one(book, google, openlib):
            outcome = seq.pop(0) if seq else (enrich.STATUS_OK, "openlibrary")
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(enrich, "enrich_one", fake_enrich_one)
        result = enrich.run(
            books,
            dry_run=True,          # nothing is written to the database
            skip_preflight=True,   # the preflight has its own tests
            sleeper=clock.sleep,
        )
        return result, clock

    return drive


@needs_db
def test_a_burst_of_instant_refusals_does_not_end_the_pass(throttling_run):
    """**The F-65 regression test.**

    Three throttles in a row used to trip the breaker, and a 403 raises with
    no retry ladder to slow it down, so the burst took about six seconds and
    every nightly pass ended before its first book. Three strikes with the
    network coming up behind them must now be survivable.
    """
    result, clock = throttling_run(
        [ProviderThrottled("HTTP 403")] * 3  # then books succeed
    )

    assert result["throttled"] is False, (
        "a burst of three instant refusals ended the pass — this is F-65"
    )
    assert result["processed"] == 5, "the remaining books should have been done"
    assert clock.slept, "it should have backed off between strikes"


@needs_db
def test_a_sustained_refusal_still_stops_the_pass(throttling_run):
    """The breaker still has to work. A provider refusing everything should
    stop the run rather than grind through thousands of books."""
    result, clock = throttling_run([ProviderThrottled("HTTP 503")] * 50, books=50)

    assert result["throttled"] is True
    assert result["processed"] == 0
    # It took real elapsed time to decide that, not three instant strikes.
    assert sum(clock.slept) >= enrich_mod.THROTTLE_MIN_SPAN


@needs_db
def test_a_book_that_works_clears_the_strikes(throttling_run):
    """Whatever the provider was doing, it is answering now. Two strikes, a
    success, two more strikes must not add up to a trip."""
    result, _ = throttling_run(
        [
            ProviderThrottled("1"),
            ProviderThrottled("2"),
            (enrich_mod.STATUS_OK, "openlibrary"),
            ProviderThrottled("3"),
            ProviderThrottled("4"),
        ]
    )

    assert result["throttled"] is False
    assert result["processed"] == 4, "the success plus the three books after it"


@needs_db
def test_the_waiting_is_bounded(throttling_run):
    """A provider failing every other book would otherwise sleep through the
    night making no progress."""
    result, clock = throttling_run([ProviderThrottled("x")] * 200, books=200)
    assert result["throttled"] is True
    assert sum(clock.slept) <= (
        enrich_mod.THROTTLE_SLEEP_BUDGET + max(enrich_mod.THROTTLE_BACKOFF)
    )
