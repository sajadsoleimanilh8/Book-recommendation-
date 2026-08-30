"""Provider, reconciliation and source-inference tests — Phase 2.

Pure-logic tests: no network, no database. They run anywhere and are fast,
which matters because they guard the two bugs that cost the most this phase —
HTML in descriptions, and F-27's source collision.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from ingest import infer_source  # noqa: E402
from providers.base import NormalizedBook, clean_isbn, isbn_10_to_13  # noqa: E402
from providers.reconcile import (  # noqa: E402
    MIN_DESCRIPTION_CHARS,
    _usable_description,
    merge,
    reconcile,
    same_book,
    strip_html,
)


# ==========================================================================
# F-27 — source inference
# ==========================================================================
#
# The bug that silently deleted 1,576 real books: the catalogue merges three
# sources, two of which use plain integer ids, and source was inferred from
# the id shape alone. Goodreads #2149 and Gutenberg #2149 are different books
# ("A Song of Ice and Fire" and "The Works of Edgar Allan Poe, Volume 3") and
# collapsing them onto one key discarded one of each pair.


@pytest.mark.parametrize(
    "book_id,thumbnail,expected",
    [
        # The exact collision from F-27.
        (2149, "https://www.gutenberg.org/cache/epub/2149/pg2149.cover.medium.jpg", "gutenberg"),
        (2149, "/static/images/default-book.jpg", "goodreads"),
        # Google Books volume ids.
        ("rOQQUJz68q8C", "http://books.google.com/books/content?id=rOQQUJz68q8C", "google_books"),
        ("rOQQUJz68q8C", None, "google_books"),
        # Fallbacks when no thumbnail is available.
        (3054, None, "goodreads"),
        ("3054", "", "goodreads"),
        # Thumbnail wins over id shape — that is the whole point.
        ("7947", "https://www.gutenberg.org/cache/epub/7947/pg7947.cover.medium.jpg", "gutenberg"),
    ],
)
def test_infer_source(book_id, thumbnail, expected):
    assert infer_source(book_id, thumbnail) == expected


def test_goodreads_and_gutenberg_ids_do_not_collide():
    """The regression itself, stated directly."""
    goodreads = infer_source(2149, "/static/images/default-book.jpg")
    gutenberg = infer_source(2149, "https://www.gutenberg.org/cache/epub/2149/pg2149.cover.medium.jpg")
    assert goodreads != gutenberg, (
        "Goodreads #2149 and Gutenberg #2149 map to the same key again — "
        "ingest will silently discard one of every colliding pair (F-27)"
    )


# ==========================================================================
# HTML stripping
# ==========================================================================
#
# Google Books returns markup inside description text. Stored raw it would
# pollute embeddings with tags-as-tokens and hand third-party HTML to the
# renderer.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<p>Hello world.</p>", "Hello world."),
        # </p><p> must become a space, not weld two words together.
        ("<p>One sentence.</p><p>Another one.</p>", "One sentence. Another one."),
        ("Line one<br/>Line two", "Line one Line two"),
        ("<i>Italic</i> and <b>bold</b>", "Italic and bold"),
        ("A &amp; B &lt;tag&gt;", "A & B <tag>"),
        ("&quot;Quoted&quot;", '"Quoted"'),
        ("  collapse   whitespace  ", "collapse whitespace"),
    ],
)
def test_strip_html(raw, expected):
    assert strip_html(raw) == expected


def test_descriptions_never_retain_markup():
    raw = "<p><b>The Wheel of Time</b> is now a series on Prime Video, starring Rosamund Pike.</p>"
    out = _usable_description(raw)
    assert out and "<" not in out and ">" not in out


def test_short_stub_is_rejected():
    """A one-line stub produces a confident embedding for a book we know
    nothing about — worse than no embedding."""
    assert _usable_description("<p>A book.</p>") is None
    assert _usable_description("x" * (MIN_DESCRIPTION_CHARS - 1)) is None
    assert _usable_description("x" * MIN_DESCRIPTION_CHARS) is not None


# ==========================================================================
# ISBN handling
# ==========================================================================

@pytest.mark.parametrize(
    "isbn10,isbn13",
    [
        ("0306406152", "9780306406157"),
        ("043942089X", "9780439420891"),
        ("0140328726", "9780140328721"),
    ],
)
def test_isbn_10_to_13(isbn10, isbn13):
    assert isbn_10_to_13(isbn10) == isbn13


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("978-0-306-40615-7", "9780306406157"),
        ("0 306 40615 2", "0306406152"),
        ("043942089x", "043942089X"),
        ("not-an-isbn", None),
        ("12345", None),
        (None, None),
        ("", None),
    ],
)
def test_clean_isbn(raw, expected):
    assert clean_isbn(raw) == expected


# ==========================================================================
# Reconciliation
# ==========================================================================

def gb(**kw) -> NormalizedBook:
    return NormalizedBook(source="google_books", **kw)


def ol(**kw) -> NormalizedBook:
    return NormalizedBook(source="open_library", **kw)


def test_same_book_by_isbn():
    assert same_book(gb(isbn_13="9780765320322"), ol(isbn_13="9780765320322"))
    assert not same_book(gb(isbn_13="9780765320322"), ol(isbn_13="9783453269866"))


def test_same_book_joins_isbn_10_against_isbn_13():
    """Without deriving a 13 from a 10, every pair where the providers
    disagree on format fails to join — common for pre-2007 titles."""
    assert same_book(gb(isbn_10="0306406152"), ol(isbn_13="9780306406157"))


def test_different_editions_are_not_merged():
    """The real case: Google returned the US edition of The Rithmatist and
    Open Library the German one. Merging would attach the wrong metadata."""
    google = gb(title="The Rithmatist", isbn_13="9780765320322", description="x" * 100)
    openlib = ol(title="Der Rithmatist", isbn_13="9783453269866", description="y" * 100)
    assert not same_book(google, openlib)
    merged, provenance = reconcile(google, openlib)
    assert provenance == "google_books"
    assert merged.isbn_13 == "9780765320322"


def test_merge_prefers_earliest_publication_year():
    """Open Library reports the work's first publication; Google reports the
    edition in hand. For 'when was this written', the work's year is right."""
    merged = merge(
        gb(title="Pride and Prejudice", published_year=1918),
        ol(title="Pride and Prejudice", published_year=1813),
    )
    assert merged.published_year == 1813


def test_merge_fills_gaps_from_the_secondary():
    merged = merge(
        gb(title="T", description="g" * 100),
        ol(title="T", isbn_13="9780306406157", page_count=250, language="en"),
    )
    assert merged.description.startswith("g")
    assert merged.isbn_13 == "9780306406157"
    assert merged.page_count == 250
    assert merged.language == "en"


def test_merge_never_invents_ratings():
    """Section 18: unknown is reported as unknown, never fabricated."""
    merged = merge(gb(title="T"), ol(title="T"))
    assert merged.average_rating is None
    assert merged.ratings_count is None


def test_reconcile_handles_a_single_provider():
    only_google, prov = reconcile(gb(title="T", description="g" * 100), None)
    assert prov == "google_books" and only_google.title == "T"

    only_ol, prov = reconcile(None, ol(title="T"))
    assert prov == "open_library"

    nothing, prov = reconcile(None, None)
    assert nothing is None and prov == "none"


def test_reconcile_records_provenance_when_merged():
    _, prov = reconcile(
        gb(title="Same Book", isbn_13="9780306406157", description="g" * 100),
        ol(title="Same Book", isbn_13="9780306406157"),
    )
    assert prov == "google_books+open_library"


def test_has_content_requires_something_worth_storing():
    assert not NormalizedBook(source="x").has_content()
    assert NormalizedBook(source="x", description="d").has_content()
    assert NormalizedBook(source="x", isbn_13="9780306406157").has_content()


# ==========================================================================
# Migration safety
# ==========================================================================

def test_not_null_columns_carry_a_server_default():
    """A NOT NULL column added without one fails on any populated table.

    This was live: `ALTER TABLE books ADD COLUMN enrichment_status
    VARCHAR(16) NOT NULL` raised NotNullViolation against the 28,399-row test
    database. It passed on the development database only because that
    database happened to be empty at that moment — so the bug would have
    surfaced first in production, on the one database guaranteed not to be
    empty.
    """
    import re
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"
    offenders = []
    for migration in versions.glob("*.py"):
        source = migration.read_text(encoding="utf-8")
        # Only add_column matters; create_table builds an empty table, so a
        # NOT NULL column there has no existing rows to violate.
        for call in re.findall(r"op\.add_column\((.*?)\)\n", source, re.DOTALL):
            if "nullable=False" in call and "server_default" not in call:
                offenders.append(f"{migration.name}: {' '.join(call.split())[:90]}")

    assert not offenders, (
        "add_column(nullable=False) without server_default will fail on a "
        "populated table:\n  " + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------------------
# F-29 — "we could not reach the provider" must never look like "the provider
# does not have this book". The first is transient and retryable; the second
# writes enrichment_status='not_found', which permanently excludes the row
# from every later pass. fetch_json used to return None for both.
# ---------------------------------------------------------------------------


def _urlopen_raising(exc):
    def fake(*_args, **_kwargs):
        raise exc

    return fake


def test_network_failure_raises_rather_than_looking_like_a_miss(monkeypatch):
    import urllib.error
    import urllib.request

    from providers.base import ProviderUnreachable, fetch_json

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        _urlopen_raising(urllib.error.URLError("getaddrinfo failed")),
    )
    # No backoff sleeping in tests.
    monkeypatch.setattr("providers.base.time.sleep", lambda *_: None)

    with pytest.raises(ProviderUnreachable):
        fetch_json("https://example.invalid/x", max_attempts=2)


def test_http_403_block_raises_rather_than_looking_like_a_miss(monkeypatch):
    """Google answers 403 with an HTML abuse page when it blocks traffic.

    403 is not in RETRY_STATUS, so it used to fall through to `return None`
    and mark the book not_found — at full speed, for every remaining book.
    """
    import io
    import urllib.error
    import urllib.request

    from providers.base import ProviderUnreachable, fetch_json

    blocked = urllib.error.HTTPError(
        "https://www.googleapis.com/books/v1/volumes",
        403,
        "Forbidden",
        {},
        io.BytesIO(b"<!DOCTYPE html><html lang=en>unusual traffic"),
    )
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(blocked))
    monkeypatch.setattr("providers.base.time.sleep", lambda *_: None)

    with pytest.raises(ProviderUnreachable):
        fetch_json("https://www.googleapis.com/books/v1/volumes", max_attempts=2)


def test_404_is_still_a_real_miss(monkeypatch):
    """The distinction has to cut both ways, or not_found becomes useless."""
    import io
    import urllib.error
    import urllib.request

    from providers.base import fetch_json

    missing = urllib.error.HTTPError("https://x/y", 404, "Not Found", {}, io.BytesIO(b""))
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_raising(missing))
    monkeypatch.setattr("providers.base.time.sleep", lambda *_: None)

    assert fetch_json("https://x/y", max_attempts=2) is None


def test_unreachable_is_handled_by_the_existing_circuit_breaker():
    """Subclassing is the mechanism that makes enrich.py need no change."""
    from providers.base import ProviderThrottled, ProviderUnreachable

    assert issubclass(ProviderUnreachable, ProviderThrottled)
