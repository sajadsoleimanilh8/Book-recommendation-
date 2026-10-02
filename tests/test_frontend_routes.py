"""Every API URL a page names must be a route the app serves — F-66.

F-66 was one page fetching `/audio/{id}`, which was never registered. It
survived because the 404 fell into a `catch` that started browser speech,
so the page always played *something*. Nothing about that failure was
visible from the outside, and no reviewer spotted it in a code comment that
openly said *"Assuming the API serves audio at /audio/{id} or similar"*.

The specific fix was one URL. The general fix is this: read the URLs out of
every page and check them against the app's own OpenAPI paths. A page that
names an endpoint nobody serves now fails a test instead of degrading
quietly in a browser.

Scope and limits, stated rather than implied:

  * Only `/api/...`-shaped URLs and the handful of legacy unprefixed routes
    are checked. Relative page links (`./book.html`) are checked separately,
    by `test_audiobook_availability.py`, against the filesystem.
  * A URL built by concatenation that this cannot see is not checked. That
    is a real gap; it is narrower than the gap of checking nothing.
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

PAGES = sorted(FRONTEND.glob("*.html"))
SCRIPTS = sorted((FRONTEND / "js").glob("*.js"))

# The three shapes a page uses to name an endpoint:
#   fetch(`${API_BASE}/api/x`)      template literal against the empty base
#   AUTH.authFetch('/api/x')        the shared helper, which prepends it
#   fetch('/api/x')                 a plain same-origin path
# The character class admits `(`, `)` and `,` so that a nested call inside
# an interpolation — `${encodeURIComponent(bookId)}`, which every one of
# these pages uses — is captured whole rather than truncated at the paren.
# Quotes and backticks are excluded, and they are what actually ends a URL.
_URL_CHARS = r"[\w/${}().,\-]*"
URL_PATTERNS = (
    re.compile(r"\$\{API_BASE\}(/" + _URL_CHARS + r")"),
    re.compile(r"authFetch\(\s*[`'\"](/" + _URL_CHARS + r")"),
    re.compile(r"fetch\(\s*[`'\"](/" + _URL_CHARS + r")"),
)


def _code_lines(text: str) -> str:
    """Drop comment lines. Prose names these URLs too, and a comment is not
    a request — the F-65 and F-66 write-ups both quote endpoints in prose.
    """
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("//", "*", "/*", "<!--", "#")):
            continue
        out.append(line)
    return "\n".join(out)


def _named_urls(text: str) -> set[str]:
    code = _code_lines(text)
    found = set()
    for pattern in URL_PATTERNS:
        for raw in pattern.findall(code):
            # Normalise JS interpolation to the OpenAPI placeholder, drop
            # any query string, and shed punctuation the capture ran into.
            path = re.sub(r"\$\{[^{}]*\}", "{}", raw).split("?")[0]
            path = path.rstrip("/,.)")
            if path:
                found.add(path)
    return found


@pytest.fixture(scope="module")
def registered() -> set[str]:
    """The app's own route table, read after startup through the schema.

    Both details matter. The routers are attached in the startup hook, so
    an import-time read sees nothing; and `include_router` wraps each one in
    an `_IncludedRouter` with no `.path`, so walking `app.routes` sees only
    the four docs endpoints. Either mistake yields a guard that passes
    without checking anything.
    """
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app):
        schema = main.app.openapi()
    paths = {re.sub(r"\{[^}]+\}", "{}", p).rstrip("/") for p in schema["paths"]}
    assert len(paths) > 20, f"only {len(paths)} routes registered; read too early?"
    return paths


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_url_a_page_names_is_served(page, registered):
    named = _named_urls(page.read_text(encoding="utf-8"))
    unknown = sorted(u for u in named if u not in registered)
    assert not unknown, (
        f"{page.name} names {len(unknown)} URL(s) the app does not serve: "
        + ", ".join(unknown)
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_url_a_shared_script_names_is_served(script, registered):
    named = _named_urls(script.read_text(encoding="utf-8"))
    unknown = sorted(u for u in named if u not in registered)
    assert not unknown, (
        f"js/{script.name} names {len(unknown)} URL(s) the app does not "
        "serve: " + ", ".join(unknown)
    )


def test_the_guard_actually_sees_urls():
    """A guard that finds nothing passes everything. F-66's own page is the
    fixture here: it must yield the audiobook endpoints."""
    named = _named_urls((FRONTEND / "Audiobook.html").read_text(encoding="utf-8"))
    assert "/api/audiobook/{}/stream" in named
    assert "/api/audiobook/generate" in named
    assert "/api/audiobook/jobs/{}" in named


def test_prose_is_not_mistaken_for_a_request():
    """`Audiobook.html` quotes the broken `/audio/{id}` in the comment that
    explains F-66. If that counted as a request the guard would fail on its
    own documentation, and the documentation would get deleted to make it
    pass."""
    named = _named_urls((FRONTEND / "Audiobook.html").read_text(encoding="utf-8"))
    assert "/audio/{}" not in named


# -- page-to-page links ---------------------------------------------------


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_page_link_resolves_on_disk(page):
    """`StaticFiles` is case-sensitive on Linux. The questionnaire linked to
    `./audiobook.html` while the file is `Audiobook.html`, so "Listen"
    worked on the Windows dev box and 404'd everywhere it was deployed
    (found with F-66). Checked for every page rather than that one.
    """
    text = _code_lines(page.read_text(encoding="utf-8"))
    links = re.findall(r'href="(?:\./)?([A-Za-z0-9_.-]+\.html)', text)

    broken = []
    for href in sorted(set(links)):
        if (FRONTEND / href).exists():
            continue
        near = [p.name for p in FRONTEND.glob("*.html") if p.name.lower() == href.lower()]
        broken.append(f"{href}" + (f" (did you mean {near[0]}?)" if near else ""))

    assert not broken, f"{page.name} links to missing page(s): " + ", ".join(broken)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_no_page_ships_a_dead_placeholder_nav(page):
    """`questionnair.html` shipped a nav of four `href="#"` links — Discover,
    Library, Audio, Settings — none of which went anywhere. A nav that looks
    like navigation and is not is worse than no nav.
    """
    text = page.read_text(encoding="utf-8")
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", text, re.S)
    if not nav:
        pytest.skip(f"{page.name} has no nav")

    dead = re.findall(r'<a[^>]*href="#"[^>]*>(.*?)</a>', nav.group(1), re.S)
    # One `href="#"` marking the current page is a convention, not a dead
    # link; several means the nav was never wired up.
    assert len(dead) <= 1, (
        f"{page.name} nav has {len(dead)} placeholder links: "
        + ", ".join(d.strip()[:20] for d in dead)
    )


# -- labels the page must have wording for --------------------------------


def test_the_book_page_covers_every_grounding_label():
    """Caught while driving the page against the live API: `book.html` had
    wording for `known_from_book`, `not_in_book` and `general_knowledge` —
    and only the first of those exists. `copilot.py` emits KNOWN,
    INFERENCE and UNCERTAIN. The two invented labels were plausible
    siblings of a real one, which is the same mistake as F-66's guessed URL.

    Read from the module rather than restated here, so adding a fourth
    label fails this instead of rendering a bare slug in the browser.
    """
    from services import copilot

    emitted = {copilot.KNOWN, copilot.INFERENCE, copilot.UNCERTAIN}
    page = (FRONTEND / "book.html").read_text(encoding="utf-8")

    block = re.search(r"const GROUNDING_TEXT = \{(.*?)\};", page, re.S)
    assert block, "book.html no longer has a GROUNDING_TEXT map"
    covered = set(re.findall(r"(\w+):\s*\[", block.group(1)))

    missing = emitted - covered
    assert not missing, (
        "book.html has no wording for grounding label(s): " + ", ".join(sorted(missing))
    )
    invented = covered - emitted
    assert not invented, (
        "book.html has wording for label(s) the backend never emits: "
        + ", ".join(sorted(invented))
    )


def test_the_upload_page_mirrors_the_servers_format_lists():
    """`mylibrary.html` checks the extension before sending a file, so a
    reader with a 50 MB book is told no without the round trip. That means
    the list exists twice, and the copy in the page can fall behind — which
    it did the moment PDF extraction shipped, leaving a page that refused a
    format the server had started accepting.

    The server stays the authority; this only keeps the convenience copy
    honest.
    """
    import main  # noqa: F401  (import order: main must load first)
    from api import library

    page = (FRONTEND / "mylibrary.html").read_text(encoding="utf-8")

    def js_list(name):
        match = re.search(rf"const {name} = \[(.*?)\]", page)
        assert match, f"{name} not found in mylibrary.html"
        return set(re.findall(r"'([a-z0-9]+)'", match.group(1)))

    assert js_list("SUPPORTED") == set(library.SUPPORTED_FORMATS), (
        "mylibrary.html's SUPPORTED list has drifted from SUPPORTED_FORMATS"
    )
    assert js_list("DEFERRED") == set(library.DEFERRED_FORMATS), (
        "mylibrary.html's DEFERRED list has drifted from DEFERRED_FORMATS"
    )
