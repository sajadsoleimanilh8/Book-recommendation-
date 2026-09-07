"""Open Library adapter — spec section 17.

Used for edition coverage, ISBN lookup, older titles and fallback metadata.
No API key, generous limits — verified working keyless while Google Books
returned 429 for anonymous requests.

Two reasons it earns its place here rather than being deferred:

* **It costs no quota.** Google Books is capped at ~1,000/day against 28,399
  books. Anything Open Library can answer is a request not spent.
* **Older titles.** A large share of this catalogue is public-domain and
  pre-2007, exactly where Google Books coverage thins and Open Library's
  edition data is strongest.

Descriptions arrive on the *work*, not the edition, so getting one costs a
second request. That is why `fetch_description` is opt-in rather than
automatic — the caller decides whether the extra round trip is worth it.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

from .base import (
    NormalizedBook,
    RateLimiter,
    clean_isbn,
    fetch_json,
    isbn_10_to_13,
    normalise_author,
    normalise_title,
)

log = logging.getLogger(__name__)

SEARCH_URL = "https://openlibrary.org/search.json"
ROOT = "https://openlibrary.org"

# Ask only for what is mapped. The default response is large and most of it
# is discarded.
SEARCH_FIELDS = ",".join(
    [
        "key", "title", "author_name", "first_publish_year", "isbn",
        "language", "number_of_pages_median", "cover_i", "subject",
    ]
)


class OpenLibraryProvider:
    name = "open_library"

    def __init__(self, min_interval: float = 0.5, fetch_description: bool = True):
        self.limiter = RateLimiter(min_interval)
        self.fetch_description = fetch_description

    @property
    def configured(self) -> bool:
        return True  # no credentials required

    # --- BookProvider ------------------------------------------------------

    def search_books(self, query: str, filters: dict | None = None) -> list[NormalizedBook]:
        limit = int((filters or {}).get("limit", 5))
        url = (
            f"{SEARCH_URL}?{urllib.parse.urlencode({'q': query, 'limit': limit, 'fields': SEARCH_FIELDS})}"
        )
        data = fetch_json(url, limiter=self.limiter)
        docs = (data or {}).get("docs") or []
        return [self._to_normalized(doc) for doc in docs[:limit]]

    def get_book(self, provider_id: str) -> NormalizedBook | None:
        if not provider_id:
            return None
        key = provider_id if provider_id.startswith("/") else f"/works/{provider_id}"
        data = fetch_json(f"{ROOT}{key}.json", limiter=self.limiter)
        if not data:
            return None
        book = self._to_normalized({"key": key, "title": data.get("title")})
        book.description = self._extract_description(data)
        return book

    def get_by_isbn(self, isbn: str) -> NormalizedBook | None:
        isbn = clean_isbn(isbn) or ""
        if not isbn:
            return None
        data = fetch_json(f"{ROOT}/isbn/{isbn}.json", limiter=self.limiter)
        if not data:
            return None

        isbn_13 = clean_isbn((data.get("isbn_13") or [None])[0]) or (
            isbn if len(isbn) == 13 else None
        )
        isbn_10 = clean_isbn((data.get("isbn_10") or [None])[0]) or (
            isbn if len(isbn) == 10 else None
        )
        if isbn_10 and not isbn_13:
            isbn_13 = isbn_10_to_13(isbn_10)

        published = str(data.get("publish_date") or "")
        year = next(
            (int(published[i : i + 4]) for i in range(len(published) - 3)
             if published[i : i + 4].isdigit()),
            None,
        )

        book = NormalizedBook(
            source="open_library",
            external_id=data.get("key"),
            title=data.get("title"),
            description=self._extract_description(data),
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            page_count=data.get("number_of_pages"),
            published_year=year,
        )

        # The edition rarely carries a description; the work usually does.
        if not book.description and self.fetch_description:
            works = data.get("works") or []
            if works:
                book.description = self._work_description(works[0].get("key"))
        return book

    # --- Enrichment entry point -------------------------------------------

    def find_for_book(self, title: str, author: str | None) -> NormalizedBook | None:
        if not title:
            return None
        title = normalise_title(title) or title
        query = f'title:"{title[:120]}"'
        first = normalise_author(author)
        if first:
            query += f' author:"{first[:60]}"'

        results = self.search_books(query, {"limit": 3})
        if not results:
            return None

        best = results[0]
        if self.fetch_description and not best.description and best.external_id:
            best.description = self._work_description(best.external_id)
        return best

    # --- Helpers -----------------------------------------------------------

    def _work_description(self, work_key: str | None) -> str | None:
        if not work_key:
            return None
        data = fetch_json(f"{ROOT}{work_key}.json", limiter=self.limiter)
        return self._extract_description(data or {})

    @staticmethod
    def _extract_description(data: dict[str, Any]) -> str | None:
        """Open Library returns either a plain string or {"type", "value"}."""
        for field in ("description", "first_sentence"):
            value = data.get(field)
            if isinstance(value, dict):
                value = value.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _to_normalized(doc: dict[str, Any]) -> NormalizedBook:
        isbns = [clean_isbn(i) for i in (doc.get("isbn") or [])]
        isbn_13 = next((i for i in isbns if i and len(i) == 13), None)
        isbn_10 = next((i for i in isbns if i and len(i) == 10), None)
        if isbn_10 and not isbn_13:
            isbn_13 = isbn_10_to_13(isbn_10)

        cover = doc.get("cover_i")
        languages = doc.get("language") or []

        return NormalizedBook(
            source="open_library",
            external_id=doc.get("key"),
            title=doc.get("title"),
            authors=list(doc.get("author_name") or []),
            categories=list((doc.get("subject") or [])[:8]),
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            language=_normalise_language(languages[0]) if languages else None,
            page_count=doc.get("number_of_pages_median"),
            published_year=doc.get("first_publish_year"),
            thumbnail=f"https://covers.openlibrary.org/b/id/{cover}-M.jpg" if cover else None,
        )


# Open Library uses MARC codes ("eng"); the catalogue uses ISO 639-1 ("en").
_MARC_TO_ISO = {
    "eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "spa": "es", "ita": "it", "por": "pt", "rus": "ru", "ara": "ar",
    "per": "fa", "fas": "fa", "chi": "zh", "jpn": "ja",
}


def _normalise_language(code: str | None) -> str | None:
    if not code:
        return None
    code = str(code).strip().lower()
    return _MARC_TO_ISO.get(code, code[:2] if len(code) >= 2 else None)
