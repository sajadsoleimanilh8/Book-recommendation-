"""Extract plain text from an uploaded TXT, EPUB or PDF — section 29's "Extract".

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


# A page of a book holds roughly 1,500-3,000 characters. A PDF whose pages
# average below this has no text layer worth the name: it is a scan, and the
# words are pixels. Averaged over the whole document, so the blank pages and
# photographic plates that any real book contains do not trip it.
MIN_CHARS_PER_PAGE = 50

# Pages are joined the way `extract_epub` joins spine items, so the chunker
# sees one shape regardless of which format the book arrived in.
PAGE_SEPARATOR = "\n\n"


class NoTextLayer(ValueError):
    """A PDF that carries images of text rather than text.

    Distinct from "extraction failed" because nothing went wrong: the file is
    valid and was read correctly, it simply has no characters in it. OCR is
    not part of section 29, and the difference matters to the reader — one is
    a bug to report, the other is "this file cannot work here".
    """


def extract_pdf(content: bytes) -> str:
    """Concatenate the text layer of every page, in page order.

    **Only a text layer.** A scanned PDF is images of words; recovering those
    needs OCR, which section 29 does not include. Rather than store the
    handful of stray characters such a file yields and call it a book, the
    chars-per-page average is checked and `NoTextLayer` is raised. Without
    that guard a 400-page scan would extract ~0 characters, chunk into
    nothing, embed nothing, and still be marked `ready` — a book in the
    reader's library that silently cannot answer a single question about
    itself. That is the F-66 shape: a path that reports success because the
    failure has no voice.

    `extract_text` is per page and joined with blank lines, which is what
    the chunker expects and matches `extract_epub`'s output shape. Reading
    order comes from pypdf and is good for single-column prose; a heavily
    multi-column layout may interleave, which is a fidelity limit worth
    knowing about rather than a failure.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(BytesIO(content))
    except PdfReadError as exc:
        raise ValueError(f"the PDF could not be parsed: {exc}") from exc

    # An encrypted PDF is common and often carries an empty user password,
    # which pypdf will accept. A real password is not something to guess at.
    if reader.is_encrypted:
        try:
            if reader.decrypt("") == 0:
                raise ValueError(
                    "the PDF is password-protected; remove the password and "
                    "upload it again"
                )
        except NotImplementedError as exc:
            # An encryption method pypdf cannot handle.
            raise ValueError(f"the PDF uses unsupported encryption: {exc}") from exc

    pages = len(reader.pages)
    if not pages:
        raise ValueError("the PDF has no pages")

    parts: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            # One unreadable page must not lose the other 399. Logged, not
            # raised, and the chars-per-page check below still has to pass.
            log.warning(f"pdf page {number}/{pages}: {type(exc).__name__}: {exc}")
            continue
        text = text.strip()
        if text:
            parts.append(text)

    joined = PAGE_SEPARATOR.join(parts)
    per_page = len(joined) / pages

    if per_page < MIN_CHARS_PER_PAGE:
        raise NoTextLayer(
            f"this PDF has almost no text layer ({len(joined)} characters "
            f"across {pages} pages). It is most likely a scan, and reading "
            "scanned pages needs OCR, which is not supported yet. A PDF "
            "exported from text, or an EPUB, will work."
        )

    log.info(
        f"pdf: {pages} page(s), {len(joined)} chars, "
        f"{per_page:.0f}/page, {len(parts)} page(s) with text"
    )
    return joined


def extract_text(file_format: str, content: bytes) -> str:
    """Dispatch on the format `api/library.py` already validated at upload."""
    if file_format == "txt":
        return extract_txt(content)
    if file_format == "epub":
        return extract_epub(content)
    if file_format == "pdf":
        return extract_pdf(content)
    raise ValueError(f"no extractor for format {file_format!r}")
