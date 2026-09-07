"""Reconcile results from multiple providers — spec section 17.

The catalogue mixes Google Books volume ids and Goodreads ids, which do not
interoperate, and title+author matching is ambiguous across editions and
translations. **ISBN is the only reliable join key**, which is why obtaining
one is a first-class goal of enrichment rather than a side effect.

Merge policy is per-field precedence, not "first provider wins". Each source
is better at different things (spec section 17):

    description   Google Books first  — longer, editorial, better for embedding
    ISBN          either; ISBN-13 preferred, derived from ISBN-10 if needed
    categories    Google Books first, Open Library subjects as fallback
    page_count    whichever is non-zero; Open Library median is often saner
    published_year  earliest known — Open Library first_publish_year is the
                  work's true first publication, Google reports the edition
    thumbnail     Google Books first, Open Library covers as fallback
    ratings       Google Books only; Open Library has none

Nothing is invented. A field absent from both stays absent, matching the
section 18 principle that unknown is reported as unknown, never fabricated.
"""

from __future__ import annotations

import html
import logging
import re

from .base import NormalizedBook, clean_isbn, isbn_10_to_13

log = logging.getLogger(__name__)

# Very short "descriptions" are usually a stub sentence or a subtitle echo.
# Embedding those is worse than embedding nothing, because it produces a
# confident vector for a book we know nothing about.
MIN_DESCRIPTION_CHARS = 40

# Google Books returns HTML in descriptions — <p>, <b>, <i>, <br> are all
# common. Storing it raw would be wrong twice over:
#
#   * embeddings: "<p>" and "<b>" become tokens and pollute the vector with
#     markup that says nothing about the book — the whole point of Phase 2 is
#     clean text to embed.
#   * display: rendered as text it looks broken; rendered as HTML it is an
#     injection vector, since this is third-party content.
#
# Strip at ingestion so every consumer downstream gets plain text and no one
# has to remember to sanitise.
_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_END_RE = re.compile(r"</(p|div|li|h[1-6])>|<br\s*/?>", re.IGNORECASE)


def strip_html(text: str) -> str:
    """HTML -> plain text, preserving paragraph breaks as spaces."""
    # Turn block boundaries into spaces first, or "</p><p>" would weld the
    # last word of one paragraph onto the first of the next.
    text = _BLOCK_END_RE.sub(" ", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _usable_description(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = strip_html(text)
    return cleaned if len(cleaned) >= MIN_DESCRIPTION_CHARS else None


def isbn_key(book: NormalizedBook) -> str | None:
    """Comparable ISBN-13 for a record, deriving one from an ISBN-10.

    Without the derivation, a pair where one provider returned a 10 and the
    other a 13 never joins — common for pre-2007 titles, which is a large
    share of this catalogue.
    """
    if book.isbn_13:
        return clean_isbn(book.isbn_13)
    if book.isbn_10:
        return isbn_10_to_13(book.isbn_10)
    return None


def same_book(a: NormalizedBook, b: NormalizedBook) -> bool:
    """Whether two provider records describe the same edition.

    ISBN match is decisive. Absent ISBNs on both sides, fall back to a
    normalised title comparison — deliberately conservative, because a false
    merge writes one book's description onto another, which is worse than
    leaving a description missing.
    """
    key_a, key_b = isbn_key(a), isbn_key(b)
    if key_a and key_b:
        return key_a == key_b
    if key_a or key_b:
        # One has an ISBN and the other does not: no evidence either way.
        # Fall through to titles rather than asserting a mismatch.
        pass

    ta = _normalise_title(a.title)
    tb = _normalise_title(b.title)
    return bool(ta and tb and ta == tb)


def _normalise_title(title: str | None) -> str:
    if not title:
        return ""
    lowered = "".join(c if c.isalnum() or c.isspace() else " " for c in title.lower())
    return " ".join(lowered.split())


def merge(primary: NormalizedBook | None, secondary: NormalizedBook | None) -> NormalizedBook | None:
    """Merge two provider records into one, applying per-field precedence.

    `primary` is Google Books by convention. Returns None only when both
    inputs are None.
    """
    if primary is None:
        return secondary
    if secondary is None:
        return primary

    merged = NormalizedBook(
        source=f"{primary.source}+{secondary.source}",
        external_id=primary.external_id or secondary.external_id,
        title=primary.title or secondary.title,
        authors=primary.authors or secondary.authors,
    )

    merged.description = (
        _usable_description(primary.description)
        or _usable_description(secondary.description)
    )

    merged.isbn_13 = clean_isbn(primary.isbn_13) or clean_isbn(secondary.isbn_13)
    merged.isbn_10 = clean_isbn(primary.isbn_10) or clean_isbn(secondary.isbn_10)
    if merged.isbn_10 and not merged.isbn_13:
        merged.isbn_13 = isbn_10_to_13(merged.isbn_10)

    merged.categories = primary.categories or secondary.categories
    merged.language = primary.language or secondary.language
    merged.page_count = primary.page_count or secondary.page_count
    merged.thumbnail = primary.thumbnail or secondary.thumbnail

    # Earliest known publication: Open Library's first_publish_year is the
    # work's, Google's is the edition in hand. The work's is the truer answer
    # for "when was this written".
    years = [y for y in (primary.published_year, secondary.published_year) if y]
    merged.published_year = min(years) if years else None

    # Ratings only exist on Google Books; never synthesise them.
    merged.average_rating = primary.average_rating or secondary.average_rating
    merged.ratings_count = primary.ratings_count or secondary.ratings_count

    return merged


def reconcile(
    google: NormalizedBook | None, openlib: NormalizedBook | None
) -> tuple[NormalizedBook | None, str]:
    """Combine two lookups into one record plus a provenance label.

    The label is stored in `books.enrichment_source` so it is always possible
    to ask afterwards where a given description came from — necessary when
    judging whether an embedding is trustworthy.
    """
    if google is None and openlib is None:
        return None, "none"

    if google is not None and openlib is not None:
        if same_book(google, openlib):
            return merge(google, openlib), "google_books+open_library"

        # They disagree about which book this is. Trust the one that answers
        # the question Phase 2 is asking, and record that it stands alone
        # rather than silently merging two different books.
        if _usable_description(google.description):
            return google, "google_books"
        if _usable_description(openlib.description):
            return openlib, "open_library"
        return google, "google_books"

    winner = google or openlib
    return winner, winner.source
