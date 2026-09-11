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
