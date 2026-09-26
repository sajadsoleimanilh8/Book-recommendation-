"""Extract plain text from an uploaded TXT or EPUB — section 29's "Extract".

Deliberately unbounded, unlike `providers/gutenberg_text.extract_reading_text`
(capped at ~20K chars — a taste of a public-domain book, shared across a
29,975-book catalogue where storing everything in full would cost ~3 GB).
An upload is the reader's own single book: storage for the whole thing is
negligible, and section 29's entire point is a private RAG assistant
grounded in *all* of it, not its opening pages. So this extracts everything.
"""

from __future__ import annotations

import logging
from io import BytesIO

from bs4 import BeautifulSoup

log = logging.getLogger("services.extraction")


def extract_txt(content: bytes) -> str:
    """Already validated as UTF-8 at upload time (api/library.py)."""
    return content.decode("utf-8")


def extract_epub(content: bytes) -> str:
    """Concatenate the spine's real chapters, in reading order.

    Not the navigation document or the NCX. `EpubHtml.is_chapter()` is
    ebooklib's own distinction for exactly this: `EpubNav` overrides it to
    `False` (verified against the library's own source, not inferred from
    one test file), and the NCX item is not an `EpubHtml` subclass at all,
    so the `ITEM_DOCUMENT` type check excludes it before `is_chapter` is
    even reached. Skipping this would prepend a page of chapter titles to
    the front of every extracted book — the same "table of contents indexed
    as though it were the book" mistake F-40 already found and fixed for
    Gutenberg text, in a different format.
    """
    import ebooklib
    from ebooklib import epub

    book = epub.read_epub(BytesIO(content))

    parts: list[str] = []
    for item_id, _linear in book.spine:
        item = book.get_item_with_id(item_id)
        if item is None:
            continue
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if not item.is_chapter():
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        text = soup.get_text(separator="\n\n", strip=True)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def extract_text(file_format: str, content: bytes) -> str:
    """Dispatch on the format `api/library.py` already validated at upload."""
    if file_format == "txt":
        return extract_txt(content)
    if file_format == "epub":
        return extract_epub(content)
    raise ValueError(f"no extractor for format {file_format!r}")
