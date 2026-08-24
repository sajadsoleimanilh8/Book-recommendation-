"""Google Books adapter — spec section 17.

Primary source for description, ISBN, categories, covers and page counts.
This is the provider that fixes F-15, the 0% description rate blocking
embeddings, semantic search and RAG.

Quota is the binding constraint, not correctness. The free tier is ~1,000
queries/day, and there are 28,399 books. The adapter therefore does one
request per book, never a speculative second, and surfaces QuotaExceeded so
the runner can stop cleanly rather than burn the remaining budget on calls
that cannot succeed.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import config

from .base import (
    NormalizedBook,
    RateLimiter,
    clean_isbn,
    fetch_json,
    isbn_10_to_13,
)

log = logging.getLogger(__name__)

API_ROOT = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooksProvider:
    name = "google_books"

    def __init__(self, api_key: str | None = None, min_interval: float = 0.35):
        self.api_key = (api_key if api_key is not None else config.GOOGLE_BOOKS_API_KEY) or ""
        self.limiter = RateLimiter(min_interval)

    @property
    def configured(self) -> bool:
        """Anonymous access is quota-exhausted in practice — verified: a
        keyless request returned 429 'Queries per day' immediately."""
        return bool(self.api_key)

    def _url(self, **params: Any) -> str:
        if self.api_key:
            params["key"] = self.api_key
        return f"{API_ROOT}?{urllib.parse.urlencode(params)}"

    # --- BookProvider ------------------------------------------------------

    def search_books(self, query: str, filters: dict | None = None) -> list[NormalizedBook]:
        limit = int((filters or {}).get("limit", 5))
        data = fetch_json(
            self._url(q=query, maxResults=min(limit, 40)), limiter=self.limiter
        )
        items = (data or {}).get("items") or []
        return [self._to_normalized(item) for item in items[:limit]]

    def get_book(self, provider_id: str) -> NormalizedBook | None:
        """Fetch by Google volume id.

        13,826 catalogue rows came from Google Books and still carry their
        volume id, so for those this is an exact lookup rather than a search —
        no ambiguity, and one request.
        """
        if not provider_id:
            return None
        url = f"{API_ROOT}/{urllib.parse.quote(provider_id)}"
        if self.api_key:
            url += f"?key={urllib.parse.quote(self.api_key)}"
        data = fetch_json(url, limiter=self.limiter)
        return self._to_normalized(data) if data else None

    def get_by_isbn(self, isbn: str) -> NormalizedBook | None:
        isbn = clean_isbn(isbn) or ""
        if not isbn:
            return None
        data = fetch_json(self._url(q=f"isbn:{isbn}", maxResults=1), limiter=self.limiter)
        items = (data or {}).get("items") or []
        return self._to_normalized(items[0]) if items else None

    # --- Enrichment entry point -------------------------------------------

    def find_for_book(self, title: str, author: str | None) -> NormalizedBook | None:
        """Best single-request match for a catalogue row.

        Uses the structured `intitle:`/`inauthor:` operators rather than a
        free-text query: a bare query matches on description text and happily
        returns a book *about* the author instead of one *by* them.
        """
        if not title:
            return None

        terms = [f'intitle:"{title[:120]}"']
        if author and author.lower() not in {"unknown", "unknown author", ""}:
            # Only the first credited author — Google matches poorly on a
            # comma-joined list of five.
            first = author.split(",")[0].strip()
            if first:
                terms.append(f'inauthor:"{first[:60]}"')

        results = self.search_books(" ".join(terms), {"limit": 3})
        if not results:
            return None

        # Prefer a result that actually carries a description; that is the
        # thing Phase 2 exists to obtain.
        for candidate in results:
            if candidate.description:
                return candidate
        return results[0]

    # --- Mapping -----------------------------------------------------------

    @staticmethod
    def _to_normalized(item: dict[str, Any]) -> NormalizedBook:
        info = item.get("volumeInfo") or {}

        isbn_10 = isbn_13 = None
        for ident in info.get("industryIdentifiers") or []:
            value = clean_isbn(ident.get("identifier"))
            if not value:
                continue
            if ident.get("type") == "ISBN_13" or len(value) == 13:
                isbn_13 = isbn_13 or value
            elif ident.get("type") == "ISBN_10" or len(value) == 10:
                isbn_10 = isbn_10 or value

        if isbn_10 and not isbn_13:
            isbn_13 = isbn_10_to_13(isbn_10)

        published = str(info.get("publishedDate") or "")
        year = int(published[:4]) if published[:4].isdigit() else None

        images = info.get("imageLinks") or {}

        return NormalizedBook(
            source="google_books",
            external_id=item.get("id"),
            title=info.get("title"),
            authors=list(info.get("authors") or []),
            description=(info.get("description") or "").strip() or None,
            categories=list(info.get("categories") or []),
            isbn_10=isbn_10,
            isbn_13=isbn_13,
            language=(info.get("language") or "").lower() or None,
            page_count=info.get("pageCount"),
            published_year=year,
            thumbnail=images.get("thumbnail") or images.get("smallThumbnail"),
            average_rating=info.get("averageRating"),
            ratings_count=info.get("ratingsCount"),
        )
