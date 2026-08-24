"""Project Gutenberg full-text adapter — descriptions from the book itself.

Why this exists
---------------
Phase 2's goal is embeddable content (F-15). For the 6,307 Gutenberg records
the metadata providers cannot supply it: measured across two samples, Open
Library returns a usable description for only **15%** of them. It finds the
books and holds no blurb.

But these *are* Gutenberg texts, and the opening pages of the actual book are
better embedding material than a marketing blurb would have been — it is the
work's real prose, in the author's voice, rather than ad copy written to sell
it. It also costs no Google Books quota.

How it stays cheap
------------------
* **Exact lookup, no search.** These rows carry the Gutenberg id as
  `external_id`, and the text URL is derivable from it, so there is no
  gutendex round trip and no chance of matching the wrong book. That halves
  the time per book (~10s to ~5s).
* **HTTP Range.** Only the first ~40 KB is requested (verified: Gutenberg
  answers 206). Downloading all 6,307 books in full would be roughly 3 GB;
  this is ~250 MB.

Extracting real prose
---------------------
The naive "first long paragraph" is wrong, and visibly so:

    Pride and Prejudice  -> "with a Preface by George Saintsbury and
                             Illustrations by Hugh Thomson"      (title page)
    Frankenstein         -> "Letter 1 Letter 2 ... Chapter 24"   (contents)
    Moby Dick            -> "This text is a combination of etexts,
                             one from the now-defunct ERIS project"  (note)

Embedding a table of contents produces a confident vector for a book whose
content the model never saw, which is worse than leaving the description
empty. `looks_like_prose` therefore rejects front matter explicitly.
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TEXT_URL = "https://www.gutenberg.org/ebooks/{book_id}.txt.utf-8"
USER_AGENT = "DigiKitab/1.0 (+https://github.com/digikitab) enrichment"

# Enough for the opening chapter of almost anything, small enough that 6,307
# books cost ~250 MB of transfer rather than ~3 GB.
FETCH_BYTES = 40_000

_START_MARKER = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", re.I | re.S
)
_END_MARKER = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", re.I
)

# Front-matter tells. Any of these disqualifies a paragraph as opening prose.
_EDITORIAL = re.compile(
    r"project gutenberg|gutenberg-tm|etext|transcriber|proofread|"
    r"public domain|copyright|illustrations? by|preface by|edited by|"
    r"translated by|produced by|distributed proofreading",
    re.I,
)
_CONTENTS = re.compile(r"\b(chapter|letter|part|book|canto|act|scene)\s+[IVXLC\d]+\b", re.I)

# A contents list does not always say "Chapter". The Adventures of Sherlock
# Holmes opens with "I. A Scandal in Bohemia II. The Red-Headed League III.
# A Case of Identity ..." — bare roman numerals and titles, which the pattern
# above misses entirely. This one catches enumerated lists of any flavour.
_ENUMERATION = re.compile(r"(?:^|\s)(?:[IVXLC]{1,6}|\d{1,3})\.\s+[A-Z]")

DESCRIPTION_TARGET_CHARS = 1200
DESCRIPTION_MAX_CHARS = 2000


def text_url(book_id: str | int) -> str:
    return TEXT_URL.format(book_id=str(book_id).strip())


def fetch_opening(book_id: str | int, *, timeout: float = 25.0) -> str | None:
    """Fetch the first FETCH_BYTES of a Gutenberg text. None if unavailable."""
    try:
        request = urllib.request.Request(
            text_url(book_id),
            headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{FETCH_BYTES}"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            log.warning(f"gutenberg {book_id}: HTTP {exc.code}")
        return None
    except Exception as exc:
        log.warning(f"gutenberg {book_id}: {type(exc).__name__} {exc}")
        return None


def strip_boilerplate(raw: str) -> str:
    """Remove the Gutenberg licence header (and footer, if the slice reached it)."""
    start = _START_MARKER.search(raw)
    body = raw[start.end():] if start else raw
    end = _END_MARKER.search(body)
    if end:
        body = body[: end.start()]
    return body.replace("\r\n", "\n").strip()


def paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def looks_like_prose(paragraph: str) -> bool:
    """Whether a paragraph is narrative text rather than front matter."""
    collapsed = " ".join(paragraph.split())
    if len(collapsed) < 200:
        return False
    if collapsed.isupper():
        return False
    if _EDITORIAL.search(collapsed):
        return False

    # A contents block is a dense run of "Chapter 1 Chapter 2 ..." or of bare
    # enumerators ("I. A Scandal in Bohemia II. The Red-Headed League ...").
    # Real prose mentions a chapter at most once or twice and does not stack
    # enumerated titles.
    if len(_CONTENTS.findall(collapsed)) >= 3:
        return False
    if len(_ENUMERATION.findall(collapsed)) >= 3:
        return False

    # Real prose has sentences and is mostly lowercase; headings, title pages
    # and running heads are not.
    if collapsed.count(". ") < 2:
        return False
    letters = [c for c in collapsed if c.isalpha()]
    if not letters or sum(c.islower() for c in letters) / len(letters) < 0.7:
        return False

    return True


def extract_description(raw: str) -> str | None:
    """Opening prose from a raw Gutenberg text slice, or None."""
    body = strip_boilerplate(raw)
    chosen: list[str] = []
    total = 0

    for para in paragraphs(body):
        if not looks_like_prose(para):
            # Skip front matter, but keep scanning — the novel is behind it.
            continue
        collapsed = " ".join(para.split())
        chosen.append(collapsed)
        total += len(collapsed)
        if total >= DESCRIPTION_TARGET_CHARS:
            break

    if not chosen:
        return None

    description = " ".join(chosen)
    if len(description) <= DESCRIPTION_MAX_CHARS:
        return description

    # Trim at a sentence boundary rather than mid-word.
    cut = description[:DESCRIPTION_MAX_CHARS]
    last_stop = cut.rfind(". ")
    return (cut[: last_stop + 1] if last_stop > DESCRIPTION_TARGET_CHARS // 2 else cut).strip()


def extract_reading_text(raw: str, max_chars: int = 20_000) -> str | None:
    """Cleaned opening text, for serving real pages instead of placeholders.

    Bounded on purpose. This closes the *fabricated content* half of F-17 —
    `/api/books/{id}/pages` currently returns the literal string
    "page1 از <title>" for every page of every book. Serving the real opening
    chapters is a genuine fix for those pages.

    It does **not** deliver whole-book reading: that needs a storage decision
    (6,307 full texts is ~3 GB), chunking for RAG, and a paging design, all of
    which are Phase 5.
    """
    body = strip_boilerplate(raw)
    if not body:
        return None
    # Keep paragraph structure — it is what makes pagination readable.
    kept, total = [], 0
    for para in paragraphs(body):
        collapsed = " ".join(para.split())
        if not collapsed:
            continue
        kept.append(collapsed)
        total += len(collapsed)
        if total >= max_chars:
            break
    return "\n\n".join(kept) if kept else None
