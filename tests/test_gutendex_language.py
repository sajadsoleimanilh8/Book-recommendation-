"""F-48's repair pass must follow gutendex pagination — and fail whole.

The first rebuild read page one only. gutendex returns 32 results per page, so
a batch of 100 ids came back as 32 and the other 68 were counted "not in
gutendex" — about a third of the corpus repaired while the run reported
plausible numbers. Found by measuring a live batch (count=100, 32 results,
next=yes), not by reading the code.

No network: urlopen is replaced with a fake that serves canned pages.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts import gutendex_language as gl  # noqa: E402


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _page(book_ids, next_url=None):
    return {
        "count": len(book_ids),
        "next": next_url,
        "results": [{"id": int(b), "languages": ["en"]} for b in book_ids],
    }


@pytest.fixture
def fake_gutendex(monkeypatch):
    """Serve `pages` in order; any entry that is an Exception is raised."""
    served: list = []

    def install(*pages):
        queue = list(pages)

        def urlopen(request, timeout=None):
            served.append(request.full_url)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Response(json.dumps(item).encode())

        monkeypatch.setattr(gl.urllib.request, "urlopen", urlopen)
        monkeypatch.setattr(gl.time, "sleep", lambda *_: None)
        return served

    return install


def test_every_page_is_followed(fake_gutendex):
    """The bug: 100 ids, three pages, and only the first was ever read."""
    ids = [str(i) for i in range(1, 101)]
    served = fake_gutendex(
        _page(ids[:32], next_url="https://gutendex.com/books?ids=x&page=2"),
        _page(ids[32:64], next_url="https://gutendex.com/books?ids=x&page=3"),
        _page(ids[64:]),
    )

    result = gl.fetch_languages(ids)

    assert len(served) == 3, "did not follow the next links"
    assert set(result) == set(ids), (
        f"{len(set(ids) - set(result))} ids missing — they would be recorded "
        "as 'not in gutendex' and never repaired"
    )


def test_a_failure_on_a_later_page_fails_the_whole_batch(fake_gutendex):
    """Returning page one's results would file every id on the failed page as
    'not in gutendex' — the same mistake by another route. All or nothing."""
    ids = [str(i) for i in range(1, 65)]
    fake_gutendex(
        _page(ids[:32], next_url="https://gutendex.com/books?ids=x&page=2"),
        urllib.error.URLError("page two timed out"),
    )

    assert gl.fetch_languages(ids) is None


def test_a_failed_request_is_distinct_from_an_empty_answer(fake_gutendex):
    """F-29: 'we could not ask' is not 'the answer is nothing'."""
    fake_gutendex(_page([]))
    assert gl.fetch_languages(["999999"]) == {}

    fake_gutendex(urllib.error.URLError("no route"))
    assert gl.fetch_languages(["999999"]) is None


def test_a_runaway_next_chain_is_stopped(fake_gutendex):
    """Against a volunteer service, an endless `next` loop is a DoS."""
    endless = _page(["1"], next_url="https://gutendex.com/books?page=again")
    fake_gutendex(*([endless] * (gl.MAX_PAGES + 5)))
    assert gl.fetch_languages(["1"]) is None


def test_batch_size_matches_the_page_size():
    """Not load-bearing — pagination is followed regardless — but a batch the
    size of one page keeps the run to one request per batch."""
    assert gl.BATCH == 32


# ---------------------------------------------------------------------------
# run()'s resilience to the database disappearing mid-run — the F-30 pattern
# from scripts/gutenberg_pass.py, applied here after it was missing and this
# exact thing happened: Docker stopped ~30 minutes into a live 4,611-book
# run, and the uncaught OperationalError killed the process outright, losing
# the in-flight batch's progress silently (the process just vanished — no
# traceback survived anywhere, no db_lost flag, nothing to report).
# ---------------------------------------------------------------------------


class _FakeBook:
    def __init__(self, pk):
        self.pk = pk
        self.language = "it"


class _FakeReadSession:
    """Serves the one-time snapshot read of (id, external_id, language)."""

    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, _query):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        return _Result(self._rows)


class _FakeWriteSession:
    """A per-batch write session.

    `.get()` hands out proxies, and a mutation only lands in `books` (the
    canonical, "as if persisted" state) on a successful `.commit()` —
    mirroring the real property under test: a write that never committed
    must not be visible afterwards, whatever run()'s counters claim.
    """

    def __init__(self, books, *, raise_on_commit=None):
        self._books = books
        self._raise_on_commit = raise_on_commit
        self._pending: dict[int, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, _model, pk):
        session = self

        class _Proxy:
            @property
            def language(self):
                return session._books[pk].language

            @language.setter
            def language(self, value):
                session._pending[pk] = value

        return _Proxy()

    def commit(self):
        if self._raise_on_commit is not None:
            raise self._raise_on_commit
        for pk, value in self._pending.items():
            self._books[pk].language = value


def test_a_lost_database_mid_run_stops_cleanly_rather_than_crashing(monkeypatch):
    """The incident, reproduced: batch 1 writes fine, batch 2's commit hits
    the database going away. run() must report it, not raise it — and must
    not count batch 2 as updated, since nothing was actually persisted."""
    from sqlalchemy.exc import OperationalError

    rows = [(1, "84", "it"), (2, "85", "it")]  # two books, two batches of one
    books = {1: _FakeBook(1), 2: _FakeBook(2)}
    sessions = iter(
        [
            _FakeReadSession(rows),
            _FakeWriteSession(books),  # batch 1: succeeds
            _FakeWriteSession(books, raise_on_commit=OperationalError("x", {}, Exception("gone"))),
        ]
    )
    monkeypatch.setattr(gl, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(gl, "BATCH", 1)
    monkeypatch.setattr(gl, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(
        gl, "fetch_languages", lambda ids: {i: "en" for i in ids}
    )

    result = gl.run(limit=10, dry_run=False)

    assert result["db_lost"] is True, "an OperationalError must be reported, not swallowed"
    assert result["updated"] == 1, (
        "only batch 1 actually committed — batch 2's failed write must not "
        "be counted as updated, or the report would claim a change that "
        "was never persisted"
    )
    assert result["batch_failed"] == 1
    assert books[1].language == "en", "batch 1's real write must still have landed"
    assert books[2].language == "it", "batch 2 must be untouched, matching the DB it never reached"


def test_a_transient_write_error_is_logged_and_the_run_continues(monkeypatch):
    """Not every DB hiccup means the database is gone. A non-OperationalError
    on one batch's write should not abort the remaining, still-reachable
    batches."""
    rows = [(1, "84", "it"), (2, "85", "it")]
    books = {1: _FakeBook(1), 2: _FakeBook(2)}
    sessions = iter(
        [
            _FakeReadSession(rows),
            _FakeWriteSession(books, raise_on_commit=RuntimeError("odd but not fatal")),
            _FakeWriteSession(books),  # batch 2: DB is fine, this succeeds
        ]
    )
    monkeypatch.setattr(gl, "SessionLocal", lambda: next(sessions))
    monkeypatch.setattr(gl, "BATCH", 1)
    monkeypatch.setattr(gl, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(gl, "fetch_languages", lambda ids: {i: "en" for i in ids})

    result = gl.run(limit=10, dry_run=False)

    assert result["db_lost"] is False
    assert result["updated"] == 1, "batch 1's failed write must not be counted"
    assert result["batch_failed"] == 1
    assert books[1].language == "it", "batch 1 never persisted"
    assert books[2].language == "en", "batch 2 must still have run and committed"
