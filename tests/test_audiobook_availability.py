"""The audiobook flag, and the route the player actually calls — F-66.

Three independent faults stacked up here, and each one hid the next:

  1. `audiobook` was `pages > 350`. That is not a statement about audio.
     It marked 7,150 books as having an audiobook, of which 236 could
     actually be synthesised, and left 6,071 that could go unflagged —
     `_gutenberg_to_api` hardcoded `False` for the one source that works,
     so the flag was closer to inverted than imprecise.

  2. The flag reached the player through `dataset.hasAudio`, and `dataset`
     values are strings. `"false"` is truthy, so *every* book took the
     real-audio branch regardless of the flag.

  3. That branch fetched `/audio/{id}`, which is not a registered route.
     Every request 404'd into a `catch` that started browser speech
     synthesis, so the failure was inaudible: the page always played
     something, just never the thing it claimed. `/api/audiobook/*` —
     info, generate, the F-19 job registry, stream — was never called by
     anything but its own tests.

The third is the one worth a permanent guard. A frontend that names a URL
the backend does not serve is not a thing a reviewer reliably notices, and
a silent fallback means no amount of clicking reveals it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

GUTENBERG = {"book_id": "12345", "thumbnail": "https://www.gutenberg.org/cache/x.jpg"}
GOOGLE = {"book_id": "rOQQUJz68q8C", "thumbnail": "https://books.google.com/books?id=x"}
GOODREADS = {"book_id": "9876543", "thumbnail": "https://images.gr-assets.com/x.jpg"}


@pytest.fixture(scope="module")
def flag():
    from services.catalogue import audiobook_available

    return audiobook_available


# -- what the flag means ---------------------------------------------------


def test_only_gutenberg_can_be_narrated(flag):
    """`AudiobookEngine.generate` calls `GutenbergClient.get_text(book_name)`.
    The text comes from Project Gutenberg over the network, not from the
    local `book_texts` table, so no other source can be synthesised at all.
    """
    assert flag(GUTENBERG) is True
    assert flag(GOOGLE) is False
    assert flag(GOODREADS) is False


@pytest.mark.parametrize("pages", [0, 1, 350, 351, 900, 5000])
def test_page_count_does_not_decide_it(flag, pages):
    """The exact bug: a long Goodreads novel is not an audiobook, and a short
    Gutenberg text is still narratable."""
    assert flag({**GOODREADS, "page_count": pages}) is False
    assert flag({**GUTENBERG, "page_count": pages}) is True


def test_an_explicit_source_is_trusted_over_inference(flag):
    """Catalogue rows carry `source` outright; only search results need
    inferring from `book_id`/`thumbnail`."""
    assert flag({"source": "gutenberg"}) is True
    assert flag({"source": "goodreads"}) is False
    # An explicit source wins even when the thumbnail would say otherwise.
    assert flag({"source": "goodreads", **GUTENBERG}) is False


def test_all_three_serialisers_agree(flag):
    """F-36 and F-54 both came from two payload builders answering one
    question differently. There are three of them here, so they are checked
    against each other rather than one at a time.

    The catalogue and ML paths delegate to the shared helper. The Gutenberg
    path states `True` outright: its rows carry neither `book_id` nor
    `thumbnail`, so inference would answer "google_books" — the same reason
    `_gutenberg_to_api` hardcodes its price constants.
    """
    from services.catalogue import _row_to_book
    from services.recommendation import _gutenberg_to_api, _ml_to_api

    for row, expected in ((GUTENBERG, True), (GOODREADS, False), (GOOGLE, False)):
        long_row = {**row, "page_count": 900, "pages": 900, "title": "x"}
        assert _row_to_book(0, long_row)["audiobook"] is expected
        assert _ml_to_api(long_row, 1)["audiobook"] is expected

    # The Gutenberg search path only ever carries Gutenberg rows.
    assert _gutenberg_to_api({"title": "x"}, 1)["audiobook"] is True


# -- the route the player calls -------------------------------------------


def test_the_player_calls_a_route_that_exists():
    """The guard for fault 3. Reads the stream URL out of the page and
    checks it against the app's registered routes.

    This fails loudly for the original `/audio/{id}`, which 404'd silently
    into a text-to-speech fallback for the entire life of the feature.
    """
    page = (FRONTEND / "Audiobook.html").read_text(encoding="utf-8")

    # Code lines only. Prose mentions these templates too — including the
    # comment that explains why API_BASE is empty — and a comment is not a
    # request.
    code = [
        line
        for line in page.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*", "<!--"))
    ]
    urls = re.findall(r"\$\{API_BASE\}(/[\w/${}.-]*)", "\n".join(code))
    assert urls, "no API_BASE-rooted URLs found in the player"

    from fastapi.testclient import TestClient

    import main

    # The routers are attached in the startup hook, not at import, so the
    # route table is empty until the app has actually started. Reading it
    # too early is how this guard would pass while proving nothing.
    # Read the paths from the OpenAPI schema rather than walking
    # `app.routes`: `include_router` wraps each router in an
    # `_IncludedRouter` with no `.path` of its own, so a flat read sees only
    # the four docs endpoints and a guard built on it would pass while
    # checking nothing. The schema is also what the routers are *for*.
    with TestClient(main.app):
        schema = main.app.openapi()
    registered = {re.sub(r"\{[^}]+\}", "{}", p) for p in schema["paths"]}

    assert any("audiobook" in p for p in registered), (
        "no audiobook routes registered — the guard cannot prove anything"
    )

    for url in urls:
        # Normalise the JS interpolations to the same placeholder.
        path = re.sub(r"\$\{[^}]+\}", "{}", url).split("?")[0]
        assert path in registered, (
            f"{path} is not a registered route. Registered audiobook routes: "
            + ", ".join(sorted(p for p in registered if "audiobook" in p))
        )


def test_the_stream_route_404s_before_anything_is_generated():
    """Why the player has to probe rather than assume. `audio_outputs` is
    empty on a fresh checkout, so "flagged as narratable" and "a file exists"
    are genuinely different questions.
    """
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        r = client.get("/api/audiobook/1/stream")
        # Either the book is not in the catalogue or nothing is generated for
        # it; both are 404, and neither is a server error.
        assert r.status_code == 404
        assert "error" in r.json()


def test_the_listen_link_matches_the_file_on_disk():
    """`StaticFiles` is case-sensitive on Linux. The questionnaire linked to
    `./audiobook.html` while the file is `Audiobook.html`, so "Listen" worked
    on the Windows dev box and 404'd everywhere it was deployed.
    """
    page = (FRONTEND / "questionnair.html").read_text(encoding="utf-8")
    for href in re.findall(r'href="\./([A-Za-z0-9_.-]+\.html)', page):
        assert (FRONTEND / href).exists(), (
            f"{href} is linked but not on disk; the nearest match is "
            + ", ".join(
                p.name for p in FRONTEND.glob("*.html") if p.name.lower() == href.lower()
            )
        )


def test_format_gives_the_same_answer_as_the_flag(flag):
    """`infer_format` was `"Audiobook" if pages > 350` — the same falsehood
    as the old flag, in a field nothing read. Closed on the product owner's
    call by deriving it, so there is one answer to the question rather than
    two that can drift apart (F-36, F-54, and F-66 itself).
    """
    from services.catalogue import infer_format

    for row in (GUTENBERG, GOODREADS, GOOGLE):
        long_row = {**row, "page_count": 900, "pages": 900}
        expected = "Audiobook" if flag(long_row) else "Print"
        assert infer_format(long_row) == expected

    # The specific case that was wrong: a long book from a source that
    # cannot be narrated.
    assert infer_format({**GOODREADS, "pages": 900}) == "Print"


# -- the page has to be able to reach a book it can play ------------------


def test_the_player_can_actually_reach_a_narratable_book():
    """Found while proving F-18 end to end, and it made the F-66 fix moot in
    practice: `/books` sorts by rating, and the books that can be narrated
    are exactly the ones with no ratings (Gutenberg), so they sit at the
    bottom of 29,975 rows. The player read `/books?limit=50` and got a list
    with nothing playable in it — fixed page, nothing to offer.
    """
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        plain = client.get("/books?limit=50").json()["items"]
        asked = client.get("/books?limit=50&audiobook=true").json()["items"]

    # The gap itself: the default list is no use to this page.
    assert sum(1 for b in plain if b["audiobook"]) == 0, (
        "the top-50-by-rating list now contains narratable books; if that is "
        "deliberate, this test should be rewritten rather than deleted"
    )
    # And the filter that closes it.
    assert asked, "no narratable books returned"
    assert all(b["audiobook"] for b in asked)
    assert all(b["format"] == "Audiobook" for b in asked), "format must agree"


def test_the_filter_is_a_filter_in_both_directions():
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        no = client.get("/books?limit=20&audiobook=false").json()["items"]

    assert no and not any(b["audiobook"] for b in no)
