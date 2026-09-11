"""F-48 — the recommender must see the database's language, not the seed's.

`site_ready_books.json` labels all 6,307 Gutenberg books `'it'`. Measured on
600 with stored text: 600 English, 0 Italian. `scripts.gutendex_language`
repairs the label in Postgres — but the recommender builds its DataFrame from
the JSON, so a database-only fix changes no recommendation. The first version
of the repair had exactly that flaw.

These tests pin the half that makes the repair reach ranking.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.catalogue import overlay_languages  # noqa: E402


def _book(source, external_id, language):
    return {"source": source, "book_id": external_id, "language": language}


def test_a_database_label_replaces_the_seed_label():
    """The F-48 case exactly: seed says Italian, Gutenberg says English."""
    books = [_book("gutenberg", "84", "It")]
    counts = overlay_languages(books, {("gutenberg", "84"): "en"})

    assert books[0]["language"] == "English"
    assert counts["changed"] == 1


def test_a_missing_database_value_leaves_the_seed_alone():
    """Absent is not a value. Blanking the seed because the database has no
    opinion would be the absent-as-negative mistake this project has made
    three times (OI-11, F-29, F-47)."""
    books = [_book("goodreads", "5", "Unknown")]
    counts = overlay_languages(books, {})

    assert books[0]["language"] == "Unknown"
    assert counts["changed"] == 0
    assert counts["no_db_value"] == 1


def test_a_null_database_value_leaves_the_seed_alone():
    books = [_book("goodreads", "5", "English")]
    overlay_languages(books, {("goodreads", "5"): None})
    assert books[0]["language"] == "English"


def test_the_key_includes_source_so_ids_cannot_collide():
    """F-27: Gutenberg and Goodreads ids are both bare integers, and treating
    them as one namespace deleted 1,576 real books per ingest. Gutenberg #84
    is Frankenstein; Goodreads #84 is something else entirely, and must not
    inherit Frankenstein's language."""
    books = [
        _book("gutenberg", "84", "It"),
        _book("goodreads", "84", "Unknown"),
    ]
    overlay_languages(books, {("gutenberg", "84"): "en"})

    assert books[0]["language"] == "English"
    assert books[1]["language"] == "Unknown", (
        "a Goodreads book inherited a Gutenberg book's language through a "
        "shared numeric id"
    )


def test_an_unchanged_label_is_not_counted_as_a_change():
    books = [_book("google_books", "abc", "English")]
    counts = overlay_languages(books, {("google_books", "abc"): "en"})
    assert counts["changed"] == 0


def test_whitespace_in_the_external_id_does_not_defeat_the_match():
    books = [_book("gutenberg", " 84 ", "It")]
    overlay_languages(books, {("gutenberg", "84"): "en"})
    assert books[0]["language"] == "English"
