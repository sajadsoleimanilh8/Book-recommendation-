from __future__ import annotations

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

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import install as install_query_param_guard
from fastapi.responses import JSONResponse

# Domain types, used only in the module-state annotations below.
from engine import QuestionerEngine, Recommender, UserProfile

# The thirteen domain routers. Every route the app serves lives in one of
# these; main.py registers them and defines none of its own.
from routes_auth import router as auth_router
from api.health import router as health_router
from api.books import router as books_router
from api.search import router as search_router
from api.recommend import router as recommend_router
from api.questionnaire import router as questionnaire_router
from api.feedback import router as feedback_router
from api.chat import router as chat_router
from api.audio import router as audio_router
from api.comments import router as comments_router
from api.reading import router as reading_router
from api.profile import router as profile_router
from api.clusters import router as clusters_router


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# Stamped on every recommendation_log row. Without it, rows from before and
# after a ranking change are indistinguishable, and the first question anyone
# asks of this table is "did the change help?".
CONTENT_VECTOR_REPORT: Dict[str, Any] = {}

MODEL_VERSION = os.getenv(
    "MODEL_VERSION", f"rec-6.0.0+{os.getenv('EMBEDDING_BACKEND', 'minilm')}"
)

app = FastAPI(
    title="DigiKitab ML API",
    description=(
        "World-class hybrid ML book recommendation system.\n\n"
        "Features: Recommendations · Audiobook · Comments · Chatbot · "
        "Reminders · Questionnaire"
    ),
    version="6.0.0",
)

# The undeclared-query-parameter guard (F-38) lives in api/middleware.py;
# installed below, after every router is registered.

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
app.include_router(health_router)
app.include_router(books_router)
app.include_router(search_router)
app.include_router(recommend_router)
app.include_router(questionnaire_router)
app.include_router(feedback_router)
app.include_router(chat_router)
app.include_router(audio_router)
app.include_router(comments_router)
app.include_router(reading_router)
app.include_router(profile_router)
app.include_router(clusters_router)

# F-38: reject unknown query parameters. After include_router so the
# guard can see every route (api/middleware.py explains the wrapper it
# has to descend into).
install_query_param_guard(app)


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

BOOKS: List[Dict[str, Any]] = []
BOOK_BY_ID: Dict[int, Dict[str, Any]] = {}
# books.id -> DataFrame row index. Built at startup so persisted user state
# can be mapped back onto the in-memory engine without a title lookup.
BOOK_IDX_BY_PK: Dict[int, int] = {}
USER_PROFILES: Dict[str, UserProfile] = {}
RECOMMENDER: Optional[Recommender] = None
QUESTIONER: Optional[QuestionerEngine] = None
DF: Optional[pd.DataFrame] = None


# error_response and get_profile stay in main.py: routers reach them as
# main.error_response / main.get_profile, and get_profile reads and
# writes USER_PROFILES above — live module state (RESTRUCTURE-NOTES
# B-4). Everything else main.py used to hold has moved to api/,
# services/, schemas/ or lifespan.py.
def error_response(msg: str, code: int = status.HTTP_400_BAD_REQUEST) -> JSONResponse:
    return JSONResponse(status_code=code, content={"error": {"message": msg}})

def get_profile(user_id: str) -> UserProfile:
    if user_id not in USER_PROFILES:
        USER_PROFILES[user_id] = UserProfile()
    return USER_PROFILES[user_id]

# Startup — catalogue load, ML fit, index build, comment rehydrate —
# moved to lifespan.py in the Phase D restructure. fit_ml went with it.
# Registered with the same @app.on_event hook the code always used
# (RESTRUCTURE-PROMPT step 15: keep the existing event style).
from lifespan import startup as _run_startup  # noqa: E402

app.on_event("startup")(_run_startup)


_SEARCH_ENCODER = None
_SEARCH_ENCODER_ERROR: Optional[str] = None


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)