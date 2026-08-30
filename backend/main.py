from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

# Loads .env from the project root if present. Optional at runtime so a
# missing python-dotenv degrades to plain environment variables rather than
# refusing to boot. GOOGLE_BOOKS_API_KEY is read from here in Phase 2.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from fastapi import FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field

import config
import models
import jobs
import ratelimit
import store
from ingest import infer_source
from auth import CurrentUser, OptionalUser, SessionDep
from db import SessionLocal
from routes_auth import router as auth_router

# Local Imports
from engine import (
    DataLoader,
    GutenbergClient,
    Recommender,
    QuestionerEngine,
    UserProfile,
)


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


app = FastAPI(
    title="DigiKitab ML API",
    description=(
        "World-class hybrid ML book recommendation system.\n\n"
        "Features: Recommendations · Audiobook · Comments · Chatbot · "
        "Reminders · Questionnaire"
    ),
    version="6.0.0",
)

# F-07 follow-on: allow_origins=["*"] with allow_credentials=True is an
# invalid combination that browsers reject outright once credentials are
# actually sent — and now that Authorization headers exist, they are. The
# wildcard was only ever needed because the frontend hardcoded a different
# origin than the page it was served from; that is a deployment blocker in
# its own right and is fixed properly when Express is retired.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000,"
            "http://localhost:8000,http://127.0.0.1:8000",
        ).split(",")
        if o.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent

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

# Set by startup() so /health can distinguish "loaded the real catalogue"
# from "silently fell back to synthetic data" - the blind spot that let F-03
# go unnoticed.
DATA_SOURCE: str = "uninitialised"

# 0 or unset means "load everything". Kept configurable so a constrained
# demo machine can cap it deliberately rather than by accident.
BOOK_LOAD_LIMIT = int(os.getenv("BOOK_LOAD_LIMIT", "0")) or None

# Single source of truth for generated audio. Must match
# AudiobookEngine.AUDIO_DIR in engine.py.
AUDIO_DIR = Path(__file__).resolve().parent / "audio_outputs"

CSV_FALLBACKS = [
    Path(__file__).parent / "merged_complete_dataset.csv",
    Path(__file__).parent / "google_books_dataset.csv",
    Path(__file__).parent / "dataset_gutenberg.csv",
    Path(__file__).parent / "bookg.csv",
]


BOOKS: List[Dict[str, Any]] = []
BOOK_BY_ID: Dict[int, Dict[str, Any]] = {}
# books.id -> DataFrame row index. Built at startup so persisted user state
# can be mapped back onto the in-memory engine without a title lookup.
BOOK_IDX_BY_PK: Dict[int, int] = {}
USER_PROFILES: Dict[str, UserProfile] = {}
RECOMMENDER: Optional[Recommender] = None
QUESTIONER: Optional[QuestionerEngine] = None
DF: Optional[pd.DataFrame] = None


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

def error_response(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": {"message": msg}})

def get_profile(user_id: str) -> UserProfile:
    if user_id not in USER_PROFILES:
        USER_PROFILES[user_id] = UserProfile()
    return USER_PROFILES[user_id]

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
    } for b in books])

def fit_ml(books: List[Dict[str, Any]]):
    if not books:
        return None, None, None

    df = _books_to_df(books)
    recommender = Recommender(df)
    recommender.fit()

    for i, b in enumerate(books):
        if i < len(recommender.df):
            b["cluster"] = int(recommender.df.loc[i, "cluster"])
        else:
            b["cluster"] = -1

    questioner = QuestionerEngine(recommender)
    return recommender, recommender.df, questioner

def apply_filters(
    data: List[Dict[str, Any]],
    genre: Optional[str] = None,
    rating_min: Optional[float] = None,
    language: Optional[str] = None,
    q: Optional[str] = None,
    max_price: Optional[float] = None,
    mood: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out = data[:]  # Create a copy
    if genre:
        out = [b for b in out if genre.lower() in b.get("genre", "").lower()]
    if language:
        out = [b for b in out if language.lower() in b.get("language", "").lower()]
    if mood:
        out = [b for b in out if mood.lower() in b.get("mood", "").lower()]
    if rating_min is not None:
        out = [b for b in out if _safe_float(b.get("rating")) >= rating_min]
    if max_price is not None:
        # OI-11. `_safe_float` turned the unknown price every non-Gutenberg row
        # carries into 0.0, so "books under $5" matched the entire catalogue —
        # a filter that silently does nothing is worse than one that is absent.
        #
        # Filter on *known* prices only. A book whose price nobody knows may
        # well cost more than the cap, and we cannot claim otherwise. That
        # leaves the Gutenberg subset, which is verifiably free, so the filter
        # is narrow but every result it returns is true.
        out = [b for b in out if price_within(b, max_price)]
    if q:
        ql = q.lower()
        out = [b for b in out if ql in b.get("title", "").lower() or ql in b.get("author", "").lower() or ql in b.get("description", "").lower()]
    return out

def _ml_to_api(b: Dict[str, Any], rank: int) -> Dict[str, Any]:
    # Same rule as the catalogue path. Two serialisers disagreeing about what
    # a book costs is how F-36 would come back.
    price, availability, inferred_source = price_and_availability(b)
    return {
        "id": b.get("id", rank),
        "title": b.get("title", "Unknown"),
        "author": b.get("author", "Unknown"),
        "genre": b.get("genre", "Unknown"),
        "mood": infer_mood(b.get("genre", ""), b.get("title", ""), b.get("description", "")),
        "language": normalize_language(b.get("language", "en")),
        "format": infer_format(_safe_int(b.get("page_count"))),
        "rating": _safe_float(b.get("average_rating")),
        "ratings_count": _safe_int(b.get("ratings_count")),
        "price": price,
        "availability": availability,
        "pages": _safe_int(b.get("page_count")),
        "audiobook": _safe_int(b.get("page_count")) > 350,
        "description": b.get("description", ""),
        "thumbnail": b.get("thumbnail", ""),
        "published_year": b.get("published_year", ""),
        "cluster": b.get("cluster", -1),
        "comment_score": round(_safe_float(b.get("comment_score")), 3),
        "ml_score": round(_safe_float(b.get("final_score")), 4),
        "content_sim": round(_safe_float(b.get("content_sim")), 3),
        "cf_sim": round(_safe_float(b.get("cf_sim")), 3),
        "source": b.get("source") or inferred_source,
        "url": b.get("url", ""),
        "rank": rank,
    }

def _gutenberg_to_api(g: Dict[str, Any], rank: int) -> Dict[str, Any]:
    return {
        "id": f"gutenberg_{rank}",
        "title": g.get("title", "Unknown"),
        "author": g.get("author", "Unknown"),
        "genre": g.get("genre", "Unknown"),
        "mood": infer_mood(g.get("genre", ""), g.get("title", ""), ""),
        "language": "English",
        "format": "Print",
        "rating": None,
        "ratings_count": None,
        "download_count": g.get("download_count", 0),
        "price": 0.0,
        "pages": None,
        "audiobook": False,
        "description": "",
        "thumbnail": "",
        "published_year": None,
        "cluster": -1,
        "comment_score": 0.0,
        "ml_score": round(g.get("download_count", 0) / 1_000_000, 4),
        "content_sim": None,
        "cf_sim": None,
        "source": "gutenberg",
        "url": g.get("url", ""),
        "rank": rank,
    }

def fuse_and_rank(
    local: List[Dict[str, Any]],
    gutenberg: List[Dict[str, Any]],
    top_n: int = 12,
) -> List[Dict[str, Any]]:
    combined = sorted(
        local + gutenberg,
        key=lambda b: _safe_float(b.get("ml_score")),
        reverse=True,
    )
    for i, b in enumerate(combined[:top_n], 1):
        b["rank"] = i
    return combined[:top_n]

class FilterRequest(BaseModel):
    genre: Optional[str] = None
    rating_min: Optional[float] = None
    language: Optional[str] = None
    mood: Optional[str] = None
    max_price: Optional[float] = None

class RecommendRequest(BaseModel):
    user_id: str = "guest"
    max_price: Optional[float] = None
    genre: Optional[str] = None
    favorite_author: Optional[str] = None
    favorite_book: Optional[str] = None
    feeling: Optional[str] = None
    min_rating: Optional[float] = None
    min_pages: Optional[int] = None
    max_pages: Optional[int] = None
    top_k: int = 12

class FeedbackRequest(BaseModel):
    user_id: str = "guest"
    book_id: int
    rating: float = Field(..., ge=1, le=5)

class ChatbotRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    user_id: str = "guest"
    book_id: Optional[int] = None

class ProgressRequest(BaseModel):
    user_id: str = "guest"
    book_id: Optional[int] = None
    progress: Optional[float] = None
    total_pages: Optional[int] = None

class CommentRequest(BaseModel):
    # user_id removed: the author is the authenticated caller (F-07).
    # extra="forbid" for the same reason as AudiobookRequest — a client that
    # still sends user_id gets a clear 422 rather than silently having its
    # value ignored while the server records someone else as the author.
    model_config = ConfigDict(extra="forbid")

    book_id: int
    comment: str = Field(..., min_length=1, max_length=1000)
    rating: Optional[int] = Field(None, ge=1, le=5)

class AudiobookRequest(BaseModel):
    # F-04: `output_file` used to be accepted from the client and passed
    # straight into os.remove() and open(..., "ab"), so a request carrying
    # {"output_file": "../server.js"} deleted and overwrote the server.
    #
    # The field is gone. The destination is now derived server-side from
    # book_id (see AudiobookEngine.generate). extra="forbid" makes a request
    # that still sends output_file fail closed with 422 rather than have it
    # silently ignored — an old client gets a clear error, not a false success.
    model_config = ConfigDict(extra="forbid")

    book_id: int = Field(..., ge=1)
    book_name: str = Field("Animal Farm", min_length=1, max_length=300)
    language: str = Field("en", min_length=2, max_length=5)

class ReminderRequest(BaseModel):
    user_id: str = "guest"
    book_id: int
    enabled: bool = True

class QuestionnaireRequest(BaseModel):
    user_id: str = "guest"
    genre: Optional[str] = Field(None, example="fantasy")
    mood: Optional[str] = Field(None, example="dark")
    pace: Optional[str] = Field(None, example="fast", description="slow | fast | any")
    language: Optional[str] = Field(None, example="en", description="en | fa | fr | de | es | any")
    popularity: Optional[str] = Field(None, example="popular", description="popular | underrated | any")
    favorite_author: Optional[str] = Field(None, example="James Clear")
    top_k: int = Field(10, ge=1, le=50)


@app.on_event("startup")
def startup():
    global BOOKS, BOOK_BY_ID, RECOMMENDER, QUESTIONER, DF, DATA_SOURCE

    log.info(f"Loading books from {DATA_FILE}…")
    # F-20 (partial): the limit=5000 cap silently truncated the catalogue to
    # 5000 of 29975 books. Configurable now, and unbounded by default.
    BOOKS = load_books_raw(limit=BOOK_LOAD_LIMIT)
    DATA_SOURCE = "json" if DATA_FILE.exists() else ("csv_fallback" if BOOKS else "none")

    if not BOOKS:
        # F-03: this path used to be reached silently and reported as
        # "csv_fallback" by /health. It is now loud, and /health says
        # "synthetic" so nobody demos fabricated data by accident.
        log.error(
            "NO REAL BOOK DATA FOUND — falling back to SYNTHETIC data. "
            f"Expected the catalogue at {DATA_FILE_CANDIDATES[0]}. "
            "Recommendations will be meaningless."
        )
        DATA_SOURCE = "synthetic"
        try:
            raw_df = DataLoader.load([])
            BOOKS = raw_df.to_dict("records")
            for i, b in enumerate(BOOKS):
                b["id"] = i + 1
        except Exception as e:
            log.error(f"Failed to load synthetic data: {e}")
            BOOKS = []
            DATA_SOURCE = "none"

    BOOK_BY_ID = {b["id"]: b for b in BOOKS}
    log.info(f"Loaded {len(BOOKS)} books.")

    if BOOKS:
        log.info("Fitting ML engine…")
        try:
            RECOMMENDER, DF, QUESTIONER = fit_ml(BOOKS)
            if RECOMMENDER:
                log.info("ML engine ready — all features active.")
        except Exception as e:
            log.error(f"ML fit failed: {e}")
            RECOMMENDER = None
            QUESTIONER = None
    else:
        log.warning("Skipping ML fit due to empty book list.")

    if RECOMMENDER is not None:
        _build_pk_index()
        _rehydrate_comments()

    # Outside the block above on purpose: semantic search reads from Postgres
    # and does not depend on the recommender, so a failed ML fit must not also
    # take search down. Failure here is recorded, never fatal.
    _get_search_encoder()


def _build_pk_index() -> None:
    """Map books.id -> DataFrame row index, once, at startup.

    Doing this per-request would be 28k lookups; doing it by title would
    collide on the 1,576 duplicate records F-25 found.
    """
    global BOOK_IDX_BY_PK
    BOOK_IDX_BY_PK = {}
    try:
        with SessionLocal() as session:
            for book in BOOKS:
                pk = store.resolve_book_pk(session, book)
                if pk is not None:
                    # First occurrence wins, matching the ingest's
                    # de-duplication so both sides agree on which row is
                    # canonical.
                    BOOK_IDX_BY_PK.setdefault(pk, store.df_index_for(book))
        log.info(f"Mapped {len(BOOK_IDX_BY_PK)} books to persistent ids.")
    except Exception as e:
        log.error(f"Could not build the book id index: {e}")


def _rehydrate_comments() -> None:
    """F-12: restore persisted comments into the ranking-facing cache.

    Before this, a restart erased every comment and reset comment_score to
    0.0. Comments are replayed in id order so the cache matches the order
    GET returns, which is what keeps delete-by-position aligned.

    Note this legitimately changes comment_score at boot, which is a ranking
    input. It cannot currently change rankings, because comment_score has
    zero LTR importance (F-26) — but that is a bug, not a guarantee. When
    Phase 3 fixes F-26 this becomes a real behavioural difference between a
    cold and a warm start, and will need the fitted model to account for it.
    """
    if RECOMMENDER is None or getattr(RECOMMENDER, "comment", None) is None:
        return
    try:
        with SessionLocal() as session:
            grouped = store.all_comments_by_book(session)
        restored = 0
        for book_pk, rows in grouped.items():
            book_idx = BOOK_IDX_BY_PK.get(book_pk)
            if book_idx is None:
                continue
            for row in rows:
                RECOMMENDER.comment.add(
                    book_idx=book_idx,
                    user_id=str(row.user_id),
                    text=row.text,
                    rating=row.rating,
                    profile=None,
                )
                restored += 1
        if restored:
            log.info(f"Rehydrated {restored} comment(s) from Postgres.")
    except Exception as e:
        log.error(f"Comment rehydration failed: {e}")


@app.get("/health")
@app.get("/api/health")
def health():
    return {
        # ok means "safe to serve real traffic": real catalogue AND a fitted
        # ML engine. Reporting ok:True with ml_ready:False would hide a dead
        # recommender behind a green check — the same blind spot as F-03.
        "ok": (
            DATA_SOURCE in ("json", "csv_fallback")
            and len(BOOKS) > 0
            and RECOMMENDER is not None
            and getattr(RECOMMENDER, "_fitted", False)
        ),
        "books_loaded": len(BOOKS),
        "ml_ready": RECOMMENDER is not None and getattr(RECOMMENDER, '_fitted', False),
        "audiobook_ready": RECOMMENDER is not None and getattr(RECOMMENDER, 'audiobook', None) is not None,
        "comments_ready": RECOMMENDER is not None and getattr(RECOMMENDER, 'comment', None) is not None,
        "chatbot_ready": RECOMMENDER is not None and getattr(RECOMMENDER, 'chatbot', None) is not None,
        "reminder_ready": RECOMMENDER is not None and getattr(RECOMMENDER, 'reminder', None) is not None,
        "questioner_ready": QUESTIONER is not None,
        # F-03: this used to report only "json" or "csv_fallback", so a boot
        # onto synthetic data was indistinguishable from a healthy one.
        # "synthetic" and "none" are now reportable, and `ok` is False for
        # both — serving fabricated books is not a healthy state.
        "data_source": DATA_SOURCE,
        "using_real_data": DATA_SOURCE in ("json", "csv_fallback"),
        # F-21: this was int(RECOMMENDER.cluster), but `cluster` is the
        # ClusteringModel object, not a number — so /health raised a 500
        # TypeError on every call where the ML engine had actually fitted.
        # The endpoint only ever returned 200 while the engine was broken,
        # which is a large part of why F-03 went unnoticed. best_k is the int.
        "clusters": getattr(getattr(RECOMMENDER, "cluster", None), "best_k", None),
        # Semantic search reports separately and does NOT gate `ok`. It
        # degrades cleanly to 503 on its own endpoint while the rest of the
        # app serves normally (section 12), so folding it into `ok` would
        # take the whole service red over one optional capability.
        #
        # But it is reported, because the F-03 and F-21 lesson is that a
        # capability nobody can see the state of is a capability that fails
        # silently. `search_ready:false` next to `ok:true` is the honest
        # shape: the app is healthy, this feature is not configured.
        "search_ready": _SEARCH_ENCODER is not None,
        "search_backend": getattr(_SEARCH_ENCODER, "name", None),
        "search_error": _SEARCH_ENCODER_ERROR,
    }

@app.get("/books")
@app.get("/api/books")
def get_books(
    limit: int = Query(24, ge=1, le=500),
    q: Optional[str] = None,
    genre: Optional[str] = None,
    mood: Optional[str] = None,
    language: Optional[str] = None,
    rating_min: Optional[float] = None,
) -> Dict[str, Any]:
    items = apply_filters(
        BOOKS,
        genre=genre,
        rating_min=rating_min,
        language=language,
        q=q,
        mood=mood
    )
    items = sorted(
        items,
        key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
        reverse=True
    )
    return {"items": items[:limit], "total": len(items)}

# F-16: /api/books/filter-options MUST stay above /api/books/{book_id}.
# FastAPI matches in declaration order, so with the catch-all first the
# literal path was captured by it and int("filter-options") failed with 422 —
# the endpoint was unreachable. Do not reorder these two.
@app.get("/api/books/filter-options")
def filter_options() -> Dict[str, Any]:
    def uniq(key: str) -> List[str]:
        return sorted({str(b.get(key, "")).strip() for b in BOOKS if b.get(key)})[:200]

    return {
        "genres": uniq("genre"),
        "authors": uniq("author"),
        "moods": uniq("mood"),
        "languages": uniq("language"),
        "clusters": sorted({b.get("cluster", -1) for b in BOOKS}),
    }


@app.get("/books/{book_id}")
@app.get("/api/books/{book_id}")
def book_detail(book_id: int):
    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)
    return b

@app.get("/search")
@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=200)) -> Dict[str, Any]:
    items = apply_filters(BOOKS, q=q)
    return {"items": items, "total": len(items)}

# ==========================================================================
# Semantic search — section 27
# ==========================================================================
# Kept separate from /api/search, which is the existing keyword filter over
# the in-memory catalogue. They answer different questions: that one finds a
# title you can already name, this one finds a book from a description of
# what you want. Replacing it would break every caller for no gain.

_SEARCH_ENCODER = None
_SEARCH_ENCODER_ERROR: Optional[str] = None


def _get_search_encoder():
    """Load the embedding backend once. Idempotent.

    Called at startup so `/health` can tell the truth about search without a
    request having happened first, and kept lazy-safe so a caller that arrives
    before or instead of that still works.

    Never at import: the fitted artefact is ~48 MB, and an app that cannot
    start because search is unconfigured is worse than one where search alone
    reports unavailable (section 12 — a missing capability degrades, it does
    not cascade). Failure here is recorded, not raised.
    """
    global _SEARCH_ENCODER, _SEARCH_ENCODER_ERROR
    if _SEARCH_ENCODER is not None or _SEARCH_ENCODER_ERROR is not None:
        return _SEARCH_ENCODER

    try:
        from embeddings import get_backend

        backend = get_backend()
        if hasattr(backend, "load") and backend.name == "lsa":
            backend.load()
        _SEARCH_ENCODER = backend
        log.info(f"semantic search ready (backend={backend.name})")
    except Exception as exc:
        _SEARCH_ENCODER_ERROR = f"{type(exc).__name__}: {exc}"
        log.warning(f"semantic search unavailable: {_SEARCH_ENCODER_ERROR}")
    return _SEARCH_ENCODER


@app.get("/api/search/semantic")
def semantic_search_route(
    user: OptionalUser,
    session: SessionDep,
    q: str = Query(..., min_length=2, max_length=400),
    limit: int = Query(10, ge=1, le=50),
    language: Optional[str] = Query(None, max_length=8),
    genre: Optional[str] = Query(None, max_length=64),
    min_year: Optional[int] = Query(None, ge=0, le=2100),
    max_year: Optional[int] = Query(None, ge=0, le=2100),
    by_passage: bool = Query(False, description="one row per passage, not per book"),
):
    # No return annotation on purpose: this returns a plain dict on success
    # and a JSONResponse when search is unconfigured, matching how the other
    # routes in this module report a degraded capability.
    """Find books from a description of what the reader wants.

    Authorization is not decided here. `user` is optional — anonymous callers
    are a supported case — and the visibility rule lives in one place,
    `search.visible_chunks`, which every retrieval path goes through. An
    endpoint cannot opt out of it by forgetting a filter.
    """
    encoder = _get_search_encoder()
    if encoder is None:
        # 503 rather than 500: this is a configuration state with a known
        # remedy, not a crash, and the message says what the remedy is.
        return error_response(
            "Semantic search is not configured on this instance. "
            "Run `python -m embed_pass --fit` to build the model.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    from search import search_books, search_chunks

    vector = encoder.encode([q])[0]
    filters = dict(
        language=language,
        genre=genre,
        min_year=min_year,
        max_year=max_year,
        # Never rank the query against vectors from a different model.
        embedding_model=encoder.name,
    )
    user_id = user.id if user else None

    if by_passage:
        hits = search_chunks(session, vector, user_id=user_id, limit=limit, **filters)
        items = [h.as_dict() for h in hits]
    else:
        items = search_books(session, vector, user_id=user_id, limit=limit, **filters)

    return {
        "query": q,
        "items": items,
        "total": len(items),
        # Named so a caller can tell which vector space produced the ranking;
        # comparing scores across backends is meaningless.
        "backend": encoder.name,
        # Honest about scope: an empty list means nothing matched, not that
        # the book does not exist. Only part of the catalogue is embedded.
        "scope": "embedded passages only",
    }


@app.post("/filter")
@app.post("/api/filter")
def filter_books(payload: FilterRequest) -> Dict[str, Any]:
    items = apply_filters(
        BOOKS,
        genre=payload.genre,
        rating_min=payload.rating_min,
        language=payload.language,
        mood=payload.mood,
        max_price=payload.max_price
    )
    items = sorted(
        items,
        key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
        reverse=True
    )
    return {"items": items, "total": len(items)}

@app.post("/recommend")
@app.post("/api/recommend")
def recommend(payload: RecommendRequest) -> Dict[str, Any]:
    profile = get_profile(payload.user_id)

    if payload.feeling:
        profile.mood = payload.feeling.lower().strip()
    if payload.genre:
        profile.preferred_genres = [g.strip() for g in payload.genre.split(",")]
    if payload.favorite_author:
        profile.preferred_authors = [payload.favorite_author.strip()]

    local_results: List[Dict[str, Any]] = []

    if RECOMMENDER and getattr(RECOMMENDER, '_fitted', False):
        if payload.favorite_book:
            ml_books = RECOMMENDER.recommend_by_book(payload.favorite_book, profile, n=payload.top_k * 2)
        else:
            ml_books = RECOMMENDER.recommend_by_profile(profile, n=payload.top_k * 2)

        filtered_ml_books = [
            b for b in ml_books
            if (
                price_within(b, payload.max_price) and
                (payload.min_rating is None or _safe_float(b.get("average_rating")) >= payload.min_rating) and
                (payload.min_pages is None or _safe_int(b.get("page_count")) >= payload.min_pages) and
                (payload.max_pages is None or _safe_int(b.get("page_count")) <= payload.max_pages)
            )
        ]

        local_results = [_ml_to_api(b, i + 1) for i, b in enumerate(filtered_ml_books)]
    else:
        items = apply_filters(
            BOOKS,
            genre=payload.genre,
            max_price=payload.max_price,
            mood=payload.feeling
        )
        items = sorted(
            items,
            key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
            reverse=True
        )[:payload.top_k]
        local_results = items

    g_query = payload.genre or payload.feeling or payload.favorite_book or "classic"
    try:
        g_books = [_gutenberg_to_api(g, i + 1) for i, g in enumerate(GutenbergClient.search(g_query))]
    except Exception:
        g_books = []

    final = fuse_and_rank(local_results, g_books, top_n=payload.top_k)

    for b in final:
        if b.get("title") and b["title"] not in profile.viewed_books:
            profile.viewed_books.append(b["title"])

    return {"items": final, "total": len(final), "ml_powered": RECOMMENDER is not None}


@app.post("/questionnaire")
@app.post("/api/questionnaire")
def questionnaire(payload: QuestionnaireRequest) -> Dict[str, Any]:
    if not QUESTIONER:
        return error_response("Questionnaire engine not ready — ML engine still loading.")

    answers = {
        "genre": (payload.genre or "").strip().lower(),
        "mood": (payload.mood or "").strip().lower(),
        "pace": (payload.pace or "any").strip().lower(),
        "language": (payload.language or "any").strip().lower(),
        "popularity": (payload.popularity or "any").strip().lower(),
        "favorite_author": (payload.favorite_author or "").strip(),
    }

    results = QUESTIONER.recommend_from_answers(answers, n=payload.top_k)

    enriched = []
    for r in results:
        match = next((b for b in BOOKS if b["title"].lower() == r["title"].lower()), None)
        enriched.append({
            **r,
            "id": match["id"] if match else None,
            "thumbnail": match["thumbnail"] if match else "",
            "price": match["price"] if match else r.get("list_price", 0),
            "audiobook": match["audiobook"] if match else r.get("page_count", 0) > 350,
            "mood": match["mood"] if match else infer_mood(
                r.get("genre", ""),
                r.get("title", ""),
                r.get("description", "")
            ),
        })

    profile = get_profile(payload.user_id)
    if answers.get("mood"):
        profile.mood = answers["mood"]
    if answers.get("genre"):
        profile.preferred_genres = [answers["genre"].capitalize()]
    if answers.get("favorite_author"):
        profile.preferred_authors = [answers["favorite_author"]]

    for b in enriched:
        t = b.get("title")
        if t and t not in profile.viewed_books:
            profile.viewed_books.append(t)

    return {
        "items": enriched,
        "total": len(enriched),
        "answers": answers,
        "ml_powered": RECOMMENDER is not None and getattr(RECOMMENDER, '_fitted', False),
    }


@app.post("/feedback")
@app.post("/api/feedback")
def feedback(payload: FeedbackRequest) -> Dict[str, Any]:
    if not RECOMMENDER or not getattr(RECOMMENDER, '_fitted', False):
        return error_response("ML engine not ready.")

    b = BOOK_BY_ID.get(payload.book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    profile = get_profile(payload.user_id)
    matches = DF[DF["title"] == b["title"]] if DF is not None else pd.DataFrame()
    if not matches.empty:
        try:
            RECOMMENDER.record_feedback(int(matches.index[0]), payload.rating, profile)
        except Exception as e:
            log.error(f"Error recording feedback: {e}")

    return {
        "ok": True,
        "message": "Feedback recorded — taste model updated.",
        "exploration_rate": round(profile.exploration_rate, 4),
        "taste_vector_dim": len(profile.taste_vector) if profile.taste_vector is not None else 0,
    }


@app.post("/chatbot")
@app.post("/api/chat")
def chatbot(payload: ChatbotRequest):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'chatbot', None):
        return error_response("Chatbot engine not ready.")

    profile = get_profile(payload.user_id)
    try:
        response = RECOMMENDER.chatbot.respond(
            user_message=payload.message,
            user_id=payload.user_id,
            profile=profile,
            book_idx=payload.book_id,
        )
    except Exception as e:
        return error_response(f"Chatbot error: {str(e)}")

    return {
        **response,
        "answer": response.get("message", ""),
        "recommendations": response.get("books", [])[:8],
        "ml_powered": True,
    }


@app.get("/api/audiobook/{book_id}")
def audiobook_info(book_id: int):
    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)
    return {
        "ok": True,
        "book": b,
        "eligible": b.get("pages", 0) > 0,
        "message": f"'{b['title']}' is ready for audiobook generation.",
    }

@app.post("/api/audiobook/generate")
def audiobook_generate(payload: AudiobookRequest, request: Request):
    # OI-5. This endpoint is unauthenticated (F-07 covered the rest of the
    # API, not this one) and synchronous (F-19): every call fetches a book and
    # synthesises speech in the request thread. A few repeat calls occupy
    # every worker, so it is a denial-of-service lever that needs no
    # credentials. The limit goes first, before any work is done.
    try:
        ratelimit.hit(
            f"ratelimit:audiobook:{ratelimit.client_ip(request)}",
            ratelimit.AUDIOBOOK_LIMIT,
            ratelimit.AUDIOBOOK_WINDOW,
        )
    except ratelimit.RateLimited as limited:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": str(limited)}},
            headers={"Retry-After": str(limited.retry_after)},
        )

    if not RECOMMENDER or not getattr(RECOMMENDER, 'audiobook', None):
        return error_response("Audiobook engine not ready.")

    # F-19. Synthesis used to run here, in the request thread, with no
    # timeout — three concurrent calls hung the entire test suite past 120
    # seconds. It now returns immediately with a job to poll.
    try:
        job = jobs.REGISTRY.submit(
            RECOMMENDER.audiobook.generate,
            book_name=payload.book_name,
            book_id=payload.book_id,
            lang=payload.language,
        )
    except jobs.Saturated as full:
        # A queue that accepts everything only moves the exhaustion. Say no.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"message": str(full)}},
            headers={"Retry-After": str(full.retry_after)},
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "ok": True,
            "job_id": job.id,
            "status": job.status,
            "poll": f"/api/audiobook/jobs/{job.id}",
        },
        headers={"Location": f"/api/audiobook/jobs/{job.id}"},
    )


@app.get("/api/audiobook/jobs/{job_id}")
def audiobook_job_status(job_id: str):
    """Poll a generation job — F-19's other half.

    404 means the job never existed *or* its result has aged out of the
    registry (15 minutes). Those are deliberately indistinguishable: a client
    that waited that long should re-request rather than be told to keep
    polling something that is gone.
    """
    job = jobs.REGISTRY.get(job_id)
    if job is None:
        return error_response("No such job.", status.HTTP_404_NOT_FOUND)
    return job.to_dict()


@app.post("/comments")
@app.post("/api/comments")
def add_comment(payload: CommentRequest, user: CurrentUser, session: SessionDep):
    """F-07: authenticated, owner is the token holder.
    F-12: written through to Postgres before responding."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    b = BOOK_BY_ID.get(payload.book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    if book_pk is None:
        return error_response(
            "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
        )

    # F-12: was DF[DF["title"] == b["title"]].index[0], which attached the
    # comment to the first book sharing a title. Positional arithmetic is
    # exact — see store.df_index_for.
    book_idx = store.df_index_for(b)
    if not (0 <= book_idx < len(RECOMMENDER.df)):
        return error_response("Book not in ML index.")

    owner_id = str(user.id)
    profile = get_profile(owner_id)

    try:
        # Updates comment_score on the in-memory DataFrame — the
        # ranking-facing cache.
        comment = RECOMMENDER.comment.add(
            book_idx=book_idx,
            user_id=owner_id,
            text=payload.comment,
            rating=payload.rating,
            profile=profile,
        )
    except Exception as e:
        return error_response(f"Comment error: {str(e)}")

    row = store.save_comment(
        session,
        user_id=user.id,
        book_pk=book_pk,
        text=comment.text,
        rating=comment.rating,
        sentiment=comment.sentiment,
        keywords=list(comment.keywords or []),
    )

    return {
        "ok": True,
        "comment": {
            "id": row.id,
            "user_id": comment.user_id,
            "text": comment.text,
            "rating": comment.rating,
            "sentiment": comment.sentiment,
            "keywords": comment.keywords,
            "timestamp": comment.timestamp,
        },
        "book_comment_score": round(float(RECOMMENDER.df.loc[book_idx, "comment_score"]), 3),
    }

@app.get("/comments/{book_id}")
@app.get("/api/comments/{book_id}")
def get_comments(book_id: int, session: SessionDep):
    """F-12: comments are read from Postgres, the source of truth. The
    in-memory store is only the ranking-facing cache now."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_idx = store.df_index_for(b)
    if not (0 <= book_idx < len(RECOMMENDER.df)):
        return error_response("Book not in ML index.")

    book_pk = store.resolve_book_pk(session, b)
    rows = store.comments_for_book(session, book_pk) if book_pk is not None else []

    return {
        "book_id": book_id,
        "book": {"title": b["title"], "author": b["author"]},
        "comments": [
            {
                "id": c.id,
                "user_id": str(c.user_id),
                "text": c.text,
                "rating": c.rating,
                "sentiment": c.sentiment,
                "keywords": c.keywords or [],
                "timestamp": c.created_at.isoformat(),
            }
            for c in rows
        ],
        "summary": RECOMMENDER.comment.summary(book_idx),
    }

@app.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user: CurrentUser, session: SessionDep):
    """Delete one's own comment, by durable id.

    F-07: previously unauthenticated with no ownership check — any caller
    could delete any comment on any book.

    F-12 / contract change: the path was
    `/api/comments/{book_id}/{comment_index}`. Positional indices are racy
    once comments are shared, persistent rows: two concurrent deletes shift
    each other's target and remove the wrong comment. Comments now carry a
    stable id, returned by POST and by GET /api/comments/{book_id}.
    """
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    row = session.get(models.Comment, comment_id)
    if row is None:
        return error_response("Comment not found.", status.HTTP_404_NOT_FOUND)

    if row.user_id != user.id:
        # 403, not 404: the caller has proven identity, and the comment is
        # readable via GET anyway, so hiding its existence buys nothing.
        return error_response(
            "You can only delete your own comments.", status.HTTP_403_FORBIDDEN
        )

    book_pk = row.book_id
    # Position within this book's comments, in the same insertion order the
    # in-memory cache is built in, so the cache stays aligned.
    ordered = store.comments_for_book(session, book_pk)
    position = next((i for i, c in enumerate(ordered) if c.id == comment_id), None)

    if not store.delete_comment(session, comment_id=comment_id, user_id=user.id):
        return error_response("Comment not found.", status.HTTP_404_NOT_FOUND)

    # Keep the ranking-facing cache and comment_score in step.
    removed_text, removed_sentiment = row.text, row.sentiment
    book_idx = BOOK_IDX_BY_PK.get(book_pk)
    if book_idx is not None and position is not None:
        try:
            RECOMMENDER.comment.delete(book_idx, position)
        except Exception:  # pragma: no cover - cache drift must not 500
            log.warning(f"Comment cache out of step for book_pk={book_pk}")

    return {
        "ok": True,
        "removed": {"id": comment_id, "text": removed_text, "sentiment": removed_sentiment},
    }


@app.post("/reminder")
@app.post("/api/reminder")
def set_reminder(payload: ReminderRequest, user: CurrentUser, session: SessionDep):
    """F-07 + F-12: authenticated, and persisted in Postgres."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")

    b = BOOK_BY_ID.get(payload.book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    if book_pk is None:
        return error_response(
            "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
        )

    try:
        reminder = RECOMMENDER.reminder.set_reminder(
            user_id=str(user.id),
            book_id=payload.book_id,
            title=b["title"],
            enabled=payload.enabled,
        )
    except Exception as e:
        return error_response(f"Reminder error: {str(e)}")

    store.set_reminder(
        session, user_id=user.id, book_pk=book_pk, enabled=payload.enabled
    )

    if reminder is None:
        return {"ok": True, "message": f"Reminder removed for '{b['title']}'"}

    return {
        "ok": True,
        "message": f"Reminder set for '{b['title']}'",
        "reminder": {
            "book_id": reminder.book_id,
            "title": reminder.title,
            "created_at": reminder.created_at,
        },
    }

@app.get("/reminder/{user_id}")
@app.get("/api/reminder/{user_id}")
def get_reminders(user_id: str, user: CurrentUser, session: SessionDep):
    """F-07 read-authz sweep: own reminders only."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")

    if user_id != str(user.id):
        return error_response(
            "You can only view your own reminders.", status.HTTP_403_FORBIDDEN
        )

    return {"user_id": user_id, "reminders": _reminder_payload(session, user)}

@app.post("/progress")
@app.post("/api/progress")
def progress(payload: ProgressRequest, user: CurrentUser, session: SessionDep) -> Dict[str, Any]:
    """F-12: reading progress persists in Postgres.
    F-07: the owner is the token holder, not a client-supplied string."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Progress tracking not ready.")

    owner_id = str(user.id)
    profile = get_profile(owner_id)

    if payload.book_id is not None and payload.progress is not None:
        b = BOOK_BY_ID.get(payload.book_id)
        if not b:
            return error_response("Book not found", status.HTTP_404_NOT_FOUND)

        book_pk = store.resolve_book_pk(session, b)
        if book_pk is None:
            return error_response(
                "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
            )

        try:
            result = RECOMMENDER.reminder.update_progress(
                user_id=owner_id,
                book_id=payload.book_id,
                progress=payload.progress,
                total_pages=payload.total_pages or b.get("pages", 0),
                profile=profile,
            )
        except Exception as e:
            return error_response(f"Progress update error: {str(e)}")

        store.save_progress(
            session,
            user_id=user.id,
            book_pk=book_pk,
            progress=payload.progress,
            page=int(payload.total_pages or 0),
        )
        return result

    return _progress_payload(session, user)


def _reminder_payload(session, user) -> List[Dict[str, Any]]:
    """Read reminders from Postgres, mapped back to positional book ids."""
    out = []
    for row in store.reminders_for_user(session, user.id):
        book_idx = BOOK_IDX_BY_PK.get(row.book_id)
        book = BOOKS[book_idx] if book_idx is not None and book_idx < len(BOOKS) else None
        out.append(
            {
                "book_id": (book_idx + 1) if book_idx is not None else None,
                "title": book["title"] if book else None,
                "enabled": row.enabled,
                "last_notified": row.last_notified,
                "created_at": row.created_at.isoformat(),
            }
        )
    return out


def _progress_payload(session, user) -> Dict[str, Any]:
    """Read progress from Postgres, mapped back to the API's positional ids."""
    items = []
    for row in store.progress_for_user(session, user.id):
        book_idx = BOOK_IDX_BY_PK.get(row.book_id)
        items.append(
            {
                # book_idx is the DataFrame row; the API exposes id = idx + 1.
                "book_id": (book_idx + 1) if book_idx is not None else None,
                "progress": row.progress,
                "percent": int(row.progress * 100),
                "page": row.page,
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return {"user_id": str(user.id), "items": items, "total": len(items)}


@app.get("/api/profile/{user_id}")
def get_user_profile(user_id: str, user: CurrentUser) -> Dict[str, Any]:
    """F-07 read-authz sweep: this exposed any user's taste profile, mood,
    viewing history and keyword affinities to any anonymous caller who could
    guess a user_id — and the default was the literal string "guest".

    A user may now read only their own profile. 403 rather than 404 because
    the caller has proven identity; the resource plainly exists.
    """
    if user_id != str(user.id):
        return error_response(
            "You can only view your own profile.", status.HTTP_403_FORBIDDEN
        )

    p = get_profile(str(user.id))
    return {
        "user_id": user_id,
        "mood": p.mood,
        "preferred_genres": p.preferred_genres,
        "preferred_authors": p.preferred_authors,
        "viewed_books": p.viewed_books[-20:],
        "exploration_rate": round(p.exploration_rate, 4),
        "has_taste_vector": p.taste_vector is not None,
        "taste_vector_dim": len(p.taste_vector) if p.taste_vector is not None else 0,
        "feedback_given": len(p.rating_history),
        "reading_speed_ppm": round(p.reading_speed_ppm, 2),
        "liked_keywords": p.liked_keywords[-10:],
        "disliked_keywords": p.disliked_keywords[-10:],
    }


@app.get("/api/clusters")
def cluster_info() -> Dict[str, Any]:
    clusters: Dict[int, Any] = {}

    for b in BOOKS:
        c = b.get("cluster", -1)
        if c not in clusters:
            clusters[c] = {"id": c, "count": 0, "genres": {}, "avg_rating": 0.0, "sample_books": []}

        clusters[c]["count"] += 1
        g = b.get("genre", "Unknown")
        clusters[c]["genres"][g] = clusters[c]["genres"].get(g, 0) + 1
        clusters[c]["avg_rating"] += _safe_float(b.get("rating"))

        if len(clusters[c]["sample_books"]) < 3:
            clusters[c]["sample_books"].append(
                {"title": b["title"], "author": b["author"]}
            )

    for c in clusters.values():
        if c["count"] > 0:
            c["avg_rating"] = round(c["avg_rating"] / c["count"], 2)
            c["top_genre"] = max(c["genres"], key=c["genres"].get) if c["genres"] else "Unknown"

    return {"clusters": list(clusters.values()), "total_clusters": len(clusters)}



@app.get("/api/audiobook/{book_id}/stream")
def audiobook_stream(book_id: int):
    """فرانت‌اند این رو صدا میزنه - استریم فایل صوتی"""
    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    # F-06: this used to serve a single global Path("audiobook.mp3") from the
    # process CWD, ignoring book_id entirely — every book streamed whatever
    # was generated last. It now reads the per-book file that
    # AudiobookEngine.generate writes. book_id is a validated int, so it
    # cannot escape the directory.
    audio_file = AUDIO_DIR / f"{book_id}.mp3"
    if not audio_file.exists() or audio_file.stat().st_size == 0:
        return error_response(
            "No audiobook generated for this book yet.",
            status.HTTP_404_NOT_FOUND,
        )

    return FileResponse(audio_file, media_type="audio/mpeg")


@app.get("/api/progress")
def get_progress_route(user: CurrentUser, session: SessionDep):
    """F-07: user_id was a query parameter, so anyone could read anyone's
    reading history. It is now the token holder, and unreadable otherwise."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Progress tracking not ready.")

    return _progress_payload(session, user)


# Characters per rendered page. ~1,800 is a mass-market paperback page.
PAGE_CHARS = 1800


@app.get("/api/books/{book_id}/pages")
def book_pages_route(
    book_id: int,
    session: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    """F-17: this returned fabricated content for every page of every book —
    the literal string "page{n} از {title}". It now serves real text where we
    hold it, and says so honestly where we do not.

    Text is currently public-domain Gutenberg only (§11), and bounded to the
    opening chapters. Whole-book reading is Phase 5.
    """
    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    record = session.get(models.BookText, book_pk) if book_pk is not None else None

    if record is None:
        # Honest empty state rather than invented prose. Section 18's
        # principle applied to content: unknown is reported, never fabricated.
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "text_available": False,
            "reason": "No readable text is available for this book yet.",
        }

    paragraphs = record.content.split("\n\n")
    pages: List[str] = []
    buffer = ""
    for para in paragraphs:
        # Break on paragraph boundaries so a page never splits mid-sentence.
        if buffer and len(buffer) + len(para) + 2 > PAGE_CHARS:
            pages.append(buffer.strip())
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer.strip():
        pages.append(buffer.strip())

    start = (page - 1) * page_size
    window = pages[start : start + page_size]

    return {
        "items": [
            {"page_number": start + i + 1, "content": text, "book_id": book_id}
            for i, text in enumerate(window)
        ],
        "total": len(pages),
        "page": page,
        "page_size": page_size,
        "text_available": True,
        "text_source": record.source,
        # The stored text is the opening chapters, not the whole book. Say so,
        # so a client never presents a partial work as complete.
        "is_complete": record.is_complete,
    }


@app.get("/api/filter-options")
def filter_options_short():
    
    return filter_options()



@app.get("/api/books/{book_id}/comments")
def get_book_comments_shortcut(book_id: int):
    
    return get_comments(book_id)


@app.get("/api/reminders")
def get_reminders_by_params(user: CurrentUser, session: SessionDep, book_id: Optional[int] = None):
    """F-07 read-authz sweep: user_id was a query parameter defaulting to
    "guest", so anyone could enumerate anyone's reminders. It is now the
    token holder.
    F-12: read from Postgres."""
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")

    reminders = _reminder_payload(session, user)
    user_id = str(user.id)

    if book_id is not None:
        reminders = [r for r in reminders if r.get("book_id") == book_id]
    
    return {
        "user_id": user_id,
        "reminders": reminders,
        "total": len(reminders)
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)