"""Catalogue loading and row-to-API mapping — services/catalogue.py.

Extracted verbatim from `main.py` in the Phase D restructure: the
small formatting helpers (_safe_float .. infer_format), the pricing and
availability logic (price_and_availability, price_within — F-36, OI-11),
_row_to_book, load_books_raw and _books_to_df.

`error_response` and `get_profile` stayed in main.py — the latter reads
and writes USER_PROFILES, main.py's live module state (RESTRUCTURE-NOTES
B-4), so it cannot move without breaking the module-state contract
conftest.py depends on.

Path constants below are the other half of this move (RESTRUCTURE-NOTES
B-6): PROJECT_ROOT, BACKEND_DIR and CSV_FALLBACKS are all computed from
`__file__`, and this module sits one directory deeper than main.py did.
`.parent`/`.parents[1]` become `.parents[1]`/`.parents[2]` throughout —
an import re-anchor, not a logic change. Proven identical below by
comparing the resolved paths before and after the move, the same check
used for ml/embeddings.py's MODEL_DIR and services/audio.py's AUDIO_DIR.

DATA_SOURCE, BOOK_LOAD_LIMIT, AUDIO_DIR and the BOOKS/BOOK_BY_ID/... state
all stay in main.py — none of them belong to catalogue *loading*, and
several are live module state read by name from tests (B-4).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ingest import infer_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = Path(__file__).resolve().parents[1]

# F-03: the dataset lives at backend/site_ready_books.json, but only
# data/ and backend/data/ were searched - neither directory exists. So
# DATA_FILE.exists() was always False, load_books_raw() fell through to the
# CSV fallbacks, and once those were removed the app booted on 3000
# synthetic books ("Book 00000" by "Author 42") with no visible error.
# The real location is now first in the list; the old paths are retained so
# a future move into data/ keeps working.
DATA_FILE_CANDIDATES = [
    BACKEND_DIR / "site_ready_books.json",
    PROJECT_ROOT / "data" / "site_ready_books.json",
    BACKEND_DIR / "data" / "site_ready_books.json",
]
DATA_FILE = next((p for p in DATA_FILE_CANDIDATES if p.exists()), DATA_FILE_CANDIDATES[0])

# F-49 (PROGRESS.md): none of these four files has ever existed in this
# repository's history. load_books_raw falls through to [] when reached,
# same as if this list were empty. Logged, not fixed — a structural move
# is not the place to change what the app does when the catalogue is
# missing.
CSV_FALLBACKS = [
    Path(__file__).resolve().parents[1] / "merged_complete_dataset.csv",
    Path(__file__).resolve().parents[1] / "google_books_dataset.csv",
    Path(__file__).resolve().parents[1] / "dataset_gutenberg.csv",
    Path(__file__).resolve().parents[1] / "bookg.csv",
]


def _safe_float(v: Any, d: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "", " ") else d
    except Exception:
        return d

def _safe_int(v: Any, d: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "", " ") else d
    except Exception:
        return d

def normalize_language(lang: Any) -> str:
    mapping = {
        "en": "English",
        "fa": "Persian",
        "ar": "Arabic",
        "fr": "French",
        "de": "German",
        "es": "Spanish",
    }
    s = str(lang).strip().lower() if lang else "unknown"
    return mapping.get(s, s.capitalize())

def overlay_languages(
    books: List[Dict[str, Any]],
    db_languages: Dict[tuple, str],
) -> Dict[str, int]:
    """Prefer the database's language over the JSON seed's — F-48.

    `site_ready_books.json` labels every one of the 6,307 Gutenberg books
    `'it'`, which `normalize_language` renders as `"It"`. Measured on 600
    with stored text: 600 English, 0 Italian. `scripts.gutendex_language`
    repairs the label in Postgres from Gutenberg's own metadata, but the
    recommender builds its frame from *this* list — so without this step the
    repair would correct the database and change no recommendation at all.
    That was the flaw in the first version of the fix.

    Section 19 settles the precedence: the JSON is seed/fallback data, the
    database is the source of truth. So a real database value wins; a
    missing one (`None`, or a row the database does not have) leaves the
    seed untouched rather than blanking it.

    Pure: takes the mapping rather than a session, so it is testable without
    Postgres and the caller owns the failure handling. Mutates `books` in
    place and returns counts.

    `db_languages` is keyed ``(source, external_id)``, matching the
    ``(source, external_id)`` uniqueness the ingest enforces (F-27).
    """
    counts = {"checked": 0, "changed": 0, "no_db_value": 0}
    for book in books:
        counts["checked"] += 1
        key = (str(book.get("source") or ""), str(book.get("book_id") or "").strip())
        db_value = db_languages.get(key)
        if not db_value:
            counts["no_db_value"] += 1
            continue
        corrected = normalize_language(db_value)
        if corrected != book.get("language"):
            book["language"] = corrected
            counts["changed"] += 1
    return counts


def infer_mood(genre: str, title: str, desc: str) -> str:
    text = f"{genre} {title} {desc}".lower()
    for mood, kws in {
        "Reflective": ["psychology", "meaning", "mind", "money", "habit", "philosophy"],
        "Adventurous": ["fantasy", "adventure", "magic", "sci-fi", "action"],
        "Motivational": ["self-help", "motivation", "success", "biography"],
        "Romantic": ["romance", "love", "passion"],
        "Dark": ["horror", "thriller", "mystery", "crime"],
        "Calm": ["fiction", "novel", "travel", "nature"],
    }.items():
        if any(k in text for k in kws):
            return mood
    return "Thoughtful"

def infer_format(pages: int) -> str:
    return "Audiobook" if pages and pages > 350 else "Print"

def price_and_availability(row: Dict[str, Any]) -> tuple[Optional[float], str, str]:
    """Return (price, availability, source) for a catalogue row — section 18.

    F-36: every row in the dataset carries list_price = 0.00 (all 29,975 of
    them), and both the API and the UI rendered 0 as "Free". That told readers
    that copyrighted Goodreads and Google Books titles were free, which is a
    claim this platform cannot make and section 18 forbids outright.

    With no availability provider configured the honest answer is "unknown".
    Gutenberg is the one real exception: public domain, verifiably free.

    Shared by both serialisers on purpose. When this lived inline, the
    recommendation path and the catalogue path could disagree about what a
    book costs, and only one of them would ever get fixed.
    """
    source = infer_source(row.get("book_id"), row.get("thumbnail"))
    raw = _safe_float(row.get("list_price") or row.get("price"))
    if source == "gutenberg":
        return 0.0, "free_public_domain", source
    if raw > 0:
        # A real figure, rather than the 0.0 placeholder every row carries.
        return raw, "listed", source
    return None, "unknown", source


def price_within(row: Dict[str, Any], cap: Optional[float]) -> bool:
    """Whether a row's price is *known* and within `cap` — OI-11.

    Unknown is not free. `_safe_float(None) == 0.0` made every unlisted book
    look like it cost nothing, so `max_price=5` returned all 29,975 rows. A
    filter that silently matches everything is worse than no filter, because
    the caller believes it worked.

    Shared by the catalogue and recommendation paths for the same reason
    `price_and_availability` is: when this logic lived inline in both, they
    could disagree, and only one would ever get fixed.
    """
    if cap is None:
        return True
    price, availability, _ = price_and_availability(row)
    return availability != "unknown" and price is not None and price <= cap


def _row_to_book(i: int, row: Dict[str, Any]) -> Dict[str, Any]:
    title = str(row.get("title") or "Unknown Title").strip()
    author = str(row.get("author") or "Unknown Author").strip()
    genre = str(row.get("genre") or row.get("search_category") or "General").strip()
    desc = str(row.get("description") or "No description available.").strip()
    pages = _safe_int(row.get("page_count") or row.get("pages"))
    price, availability, source = price_and_availability(row)
    rating = _safe_float(row.get("average_rating") or row.get("rating"))
    rc = _safe_int(row.get("ratings_count"))
    lang = normalize_language(row.get("language"))
    pub_yr = row.get("published_year") or row.get("publication_year") or ""

    return {
        "id": i + 1,
        "book_id": row.get("book_id", ""),
        "title": title,
        "author": author,
        "genre": genre,
        "mood": infer_mood(genre, title, desc),
        "language": lang,
        "format": infer_format(pages),
        "rating": rating,
        "ratings_count": rc,
        "price": price,
        "availability": availability,
        "source": source,
        "pages": pages,
        "audiobook": pages > 350,
        "description": desc,
        "thumbnail": row.get("thumbnail", ""),
        "published_year": pub_yr,
        "cluster": -1,
    }

def load_books_raw(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Load the catalogue. limit=None means the whole file (F-20)."""
    if DATA_FILE.exists():
        with DATA_FILE.open("r", encoding="utf-8") as f:
            first = f.read(1)
            f.seek(0)
            if first == "[":
                try:
                    rows = json.load(f)
                except Exception:
                    rows = []
            else:
                rows = []
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            continue
        return [_row_to_book(i, r) for i, r in enumerate(rows[:limit])]

    csv_path = next((p for p in CSV_FALLBACKS if p.exists()), None)
    if not csv_path:
        return []

    books = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if limit is not None and i >= limit:
                break
            books.append(_row_to_book(i, row))
    return books

def _books_to_df(books: List[Dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{
        "title": b["title"],
        "author": b["author"],
        "genre": b["genre"],
        "description": b["description"],
        "average_rating": b["rating"],
        "ratings_count": b["ratings_count"],
        "page_count": b["pages"],
        # 0.0 rather than None where the price is unknown (F-36). This is a
        # feature column, not a claim: `None` becomes NaN and
        # GradientBoostingRegressor refuses to fit, which silently disabled
        # the whole recommender the first time this was changed.
        #
        # 0.0 also keeps the ranking numerically identical to before, because
        # the engine's only use of it is `1/(1+list_price)` — which was
        # already a constant 1.0 for all 29,975 books, since every row carries
        # a 0.00 placeholder. That dead feature is more evidence for F-26 and
        # belongs to Phase 3's ranking work, not to a presentation fix.
        "list_price": b["price"] if b["price"] is not None else 0.0,
        "language": b["language"],
        "published_year": _safe_int(b.get("published_year"), 2000),
        # F-26: the relevance target's evidence, attached at startup by
        # lifespan._attach_reading_depth. Used for the TARGET only, never as a
        # feature — a column that is both would let the model learn
        # "relevance equals this column" and nothing else.
        #
        # 0 rather than None when a book has no readers, for the reason given
        # above for list_price: a NaN anywhere stops the ranker fitting. Zero
        # readers is also the honest value, and `shrink` treats it as "use the
        # prior", never as "this book is bad".
        "reading_depth": float(b.get("reading_depth") or 0.0),
        "reading_depth_n": int(b.get("reading_depth_n") or 0),
    } for b in books])

