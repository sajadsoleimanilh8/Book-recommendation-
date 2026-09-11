"""Gutendex search client — the GutenbergClient.

Extracted verbatim from `engine.py` in the Phase C restructure.

Distinct from `gutenberg_text.py` in this package, which holds the
enrichment-pass functions (fetch_opening, extract_description, ...).
This module is the older, simpler search/text client `Recommender` and
`AudiobookEngine` call directly; the two were never merged because
nothing needed them to be, and merging is out of scope for a mechanical
extraction.
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class GutenbergClient:
    BASE = "https://gutendex.com/books"

    @classmethod
    def search(cls, query: str, n: int = 10) -> list[dict]:
        try:
            r = requests.get(cls.BASE, params={"search": query}, timeout=8)
            r.raise_for_status()
            results = r.json().get("results", [])

            return [
                {
                    "title": b.get("title", "Unknown"),
                    "author": ", ".join(a["name"] for a in b.get("authors", [])),
                    "genre": ", ".join(b.get("subjects", [])) or "Unknown",
                    "average_rating": None,
                    "ratings_count": None,
                    "download_count": b.get("download_count", 0),
                    "page_count": None,
                    "list_price": 0.0,
                    "source": "gutenberg",
                    "url": b.get("formats", {}).get("text/plain; charset=utf-8", ""),
                    "final_score": b.get("download_count", 0) / 1_000_000,
                }
                for b in results[:n]
            ]
        except requests.RequestException as e:
            log.warning(f"Gutenberg search failed: {e}")
            return []

    @classmethod
    def get_text(cls, book_name: str) -> str:
        r = requests.get(cls.BASE, params={"search": book_name}, timeout=10)
        r.raise_for_status()
        results = r.json().get("results", [])

        if not results:
            raise ValueError(f"No Gutenberg results for '{book_name}'.")

        text_url = results[0].get("formats", {}).get("text/plain; charset=utf-8", "")

        if not text_url:
            raise ValueError("No plain-text format available.")

        return requests.get(text_url, timeout=60).text
