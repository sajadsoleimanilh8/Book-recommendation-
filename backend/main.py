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

from fastapi import FastAPI, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, ConfigDict, Field

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

def _row_to_book(i: int, row: Dict[str, Any]) -> Dict[str, Any]:
    title = str(row.get("title") or "Unknown Title").strip()
    author = str(row.get("author") or "Unknown Author").strip()
    genre = str(row.get("genre") or row.get("search_category") or "General").strip()
    desc = str(row.get("description") or "No description available.").strip()
    pages = _safe_int(row.get("page_count") or row.get("pages"))
    price = _safe_float(row.get("list_price") or row.get("price"))
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
        "list_price": b["price"],
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
        out = [b for b in out if _safe_float(b.get("price")) <= max_price]
    if q:
        ql = q.lower()
        out = [b for b in out if ql in b.get("title", "").lower() or ql in b.get("author", "").lower() or ql in b.get("description", "").lower()]
    return out

def _ml_to_api(b: Dict[str, Any], rank: int) -> Dict[str, Any]:
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
        "price": _safe_float(b.get("list_price")),
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
        "source": b.get("source", "local"),
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
    user_id: str = "guest"
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


@app.get("/health")
@app.get("/api/health")
def health():
    return {
        "ok": DATA_SOURCE in ("json", "csv_fallback") and len(BOOKS) > 0,
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
        "clusters": int(getattr(RECOMMENDER, 'cluster', None)) if RECOMMENDER and getattr(RECOMMENDER, 'cluster', None) else None,
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
                (payload.max_price is None or _safe_float(b.get("list_price")) <= payload.max_price) and
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
def audiobook_generate(payload: AudiobookRequest):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'audiobook', None):
        return error_response("Audiobook engine not ready.")

    try:
        result = RECOMMENDER.audiobook.generate(
            book_name=payload.book_name,
            book_id=payload.book_id,
            lang=payload.language,
        )
    except Exception as e:
        return error_response(f"Generation error: {str(e)}")

    code = status.HTTP_200_OK if result.get("ok") else status.HTTP_422_UNPROCESSABLE_ENTITY
    return JSONResponse(status_code=code, content=result)


@app.post("/comments")
@app.post("/api/comments")
def add_comment(payload: CommentRequest):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    b = BOOK_BY_ID.get(payload.book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    matches = DF[DF["title"] == b["title"]] if DF is not None else pd.DataFrame()
    if matches.empty:
        return error_response("Book not in ML index.")

    book_idx = int(matches.index[0])
    profile = get_profile(payload.user_id)

    try:
        comment = RECOMMENDER.comment.add(
            book_idx=book_idx,
            user_id=payload.user_id,
            text=payload.comment,
            rating=payload.rating,
            profile=profile,
        )
    except Exception as e:
        return error_response(f"Comment error: {str(e)}")

    return {
        "ok": True,
        "comment": {
            "user_id": comment.user_id,
            "text": comment.text,
            "rating": comment.rating,
            "sentiment": comment.sentiment,
            "keywords": comment.keywords,
            "timestamp": comment.timestamp,
        },
        "book_comment_score": round(float(RECOMMENDER.df.loc[book_idx, "comment_score"]), 3) if book_idx < len(RECOMMENDER.df) else 0.0,
    }

@app.get("/comments/{book_id}")
@app.get("/api/comments/{book_id}")
def get_comments(book_id: int):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    matches = DF[DF["title"] == b["title"]] if DF is not None else pd.DataFrame()
    if matches.empty:
        return error_response("Book not in ML index.")

    book_idx = int(matches.index[0])

    return {
        "book_id": book_id,
        "book": {"title": b["title"], "author": b["author"]},
        "comments": RECOMMENDER.comment.get(book_idx),
        "summary": RECOMMENDER.comment.summary(book_idx),
    }

@app.delete("/comments/{book_id}/{comment_index}")
@app.delete("/api/comments/{book_id}/{comment_index}")
def delete_comment(book_id: int, comment_index: int):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'comment', None):
        return error_response("Comment engine not ready.")

    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    matches = DF[DF["title"] == b["title"]] if DF is not None else pd.DataFrame()
    if matches.empty:
        return error_response("Book not in ML index.")

    try:
        removed = RECOMMENDER.comment.delete(int(matches.index[0]), comment_index)
    except Exception:
        return error_response("Invalid comment index.")

    if removed is None:
        return error_response("Invalid comment index.")

    return {
        "ok": True,
        "removed": {"text": removed.text, "sentiment": removed.sentiment},
    }


@app.post("/reminder")
@app.post("/api/reminder")
def set_reminder(payload: ReminderRequest):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")

    b = BOOK_BY_ID.get(payload.book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)

    try:
        reminder = RECOMMENDER.reminder.set_reminder(
            user_id=payload.user_id,
            book_id=payload.book_id,
            title=b["title"],
            enabled=payload.enabled,
        )
    except Exception as e:
        return error_response(f"Reminder error: {str(e)}")

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
def get_reminders(user_id: str):
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")

    return {
        "user_id": user_id,
        "reminders": RECOMMENDER.reminder.get_user_reminders(user_id),
    }

@app.post("/progress")
@app.post("/api/progress")
def progress(payload: ProgressRequest) -> Dict[str, Any]:
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Progress tracking not ready.")

    profile = get_profile(payload.user_id)

    if payload.book_id is not None and payload.progress is not None:
        b = BOOK_BY_ID.get(payload.book_id)
        if not b:
            return error_response("Book not found", status.HTTP_404_NOT_FOUND)

        try:
            return RECOMMENDER.reminder.update_progress(
                user_id=payload.user_id,
                book_id=payload.book_id,
                progress=payload.progress,
                total_pages=payload.total_pages or b.get("pages", 0),
                profile=profile,
            )
        except Exception as e:
            return error_response(f"Progress update error: {str(e)}")

    items = RECOMMENDER.reminder.get_progress(payload.user_id)
    return {"user_id": payload.user_id, "items": items, "total": len(items)}


@app.get("/api/profile/{user_id}")
def get_user_profile(user_id: str) -> Dict[str, Any]:
    p = get_profile(user_id)
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
def get_progress_route(user_id: str = "guest"):
    
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Progress tracking not ready.")
    
    items = RECOMMENDER.reminder.get_progress(user_id)
    return {"user_id": user_id, "items": items, "total": len(items)}


@app.get("/api/books/{book_id}/pages")
def book_pages_route(book_id: int, page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100)):
    
    b = BOOK_BY_ID.get(book_id)
    if not b:
        return error_response("Book not found", status.HTTP_404_NOT_FOUND)
    
    total_pages = b.get("pages", 100)
    start = (page - 1) * page_size
    end = min(start + page_size, total_pages)
    
    items = []
    for i in range(start, end):
        items.append({
            "page_number": i + 1,
            "content": f"page{i+1} از {b.get('title', 'book')}",
            "book_id": book_id
        })
    
    return {
        "items": items,
        "total": total_pages,
        "page": page,
        "page_size": page_size
    }


@app.get("/api/filter-options")
def filter_options_short():
    
    return filter_options()



@app.get("/api/books/{book_id}/comments")
def get_book_comments_shortcut(book_id: int):
    
    return get_comments(book_id)


@app.get("/api/reminders")
def get_reminders_by_params(user_id: str = "guest", book_id: int = None):
    
    if not RECOMMENDER or not getattr(RECOMMENDER, 'reminder', None):
        return error_response("Reminder engine not ready.")
    
    reminders = RECOMMENDER.reminder.get_user_reminders(user_id)
    
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