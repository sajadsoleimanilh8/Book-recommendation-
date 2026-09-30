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
from fastapi.staticfiles import StaticFiles

from core import config

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
from api.library import router as library_router
from api.copilot import router as copilot_router


log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


# Stamped on every recommendation_log row. Without it, rows from before and
# after a ranking change are indistinguishable, and the first question anyone
# asks of this table is "did the change help?".
CONTENT_VECTOR_REPORT: Dict[str, Any] = {}
# F-26: how much reading-depth evidence the ranker trained on. Empty until
# startup runs.
READING_DEPTH_REPORT: Dict[str, Any] = {}
# F-48: what the startup language overlay did. Empty until startup runs.
LANGUAGE_OVERLAY: Dict[str, Any] = {}

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
    # OI-5: the interactive docs are a complete, self-service map of every
    # route, parameter and schema in the app, served to anyone. That is a
    # feature while the only caller is the person building it, and pure
    # reconnaissance once the app is reachable. Gated on ENV rather than
    # deleted, because they earn their keep in development.
    docs_url="/docs" if not config.IS_PRODUCTION else None,
    redoc_url="/redoc" if not config.IS_PRODUCTION else None,
    openapi_url="/openapi.json" if not config.IS_PRODUCTION else None,
)

# The undeclared-query-parameter guard (F-38) lives in api/middleware.py;
# installed below, after every router is registered.

# F-07 follow-on: allow_origins=["*"] with allow_credentials=True is an
# invalid combination that browsers reject outright once credentials are
# actually sent — and now that Authorization headers exist, they are.
#
# OI-5: the default is now **no cross-origin caller at all**, which is the
# real fix rather than a narrower allowlist. The wildcard, and then the
# localhost allowlist that replaced it, existed only because Express served
# the pages from `:3000` while the API answered on `:8000`. Express is
# retired and this app serves its own frontend (see the mount at the bottom
# of this file), so every request the browser makes is same-origin and CORS
# has nothing left to permit. A list that defaults to localhost is a
# deployment gate; a list that defaults to empty is not.
#
# `CORS_ORIGINS` stays as a deliberate escape hatch — for anyone serving the
# frontend separately during development, or a future second client — but it
# now has to be asked for.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
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
app.include_router(library_router)
app.include_router(copilot_router)

# F-38: reject unknown query parameters. After include_router so the
# guard can see every route (api/middleware.py explains the wrapper it
# has to descend into).
install_query_param_guard(app)


# OI-5: this app serves its own frontend, and Express is retired.
#
# `server.js` was an Express process on :3000 that served `frontend/` as
# static files, spawned this backend as a child, and offered an `/api` proxy
# that nothing used and that did not work — it stripped the `/api` prefix, so
# every `/api`-only route 404'd through it; it dropped `Authorization`, so no
# authenticated request could have survived it; and it called
# `response.json()` unconditionally, so the audio `FileResponse` came back as
# a fabricated 502. Every page reached :8000 directly instead, which is why
# none of that was ever noticed (PHASE-0-AUDIT, "dead /api proxy").
#
# Two origins for one app is what forced `allow_origins` to list localhost,
# and an allowlist that has to name a host is a thing you must remember to
# change before deploying. One origin removes the setting rather than
# tightening it.
#
# Mounted last, and at "/", so it is the fallback: Starlette matches routes
# in registration order, so every API route and `/docs` is already claimed by
# the time a request reaches here. `html=True` serves `index.html` for "/"
# and resolves `/filter.html` and friends, which is what Express's per-page
# `sendFile` routes were doing by hand.
FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:  # pragma: no cover - a backend-only checkout is still a valid API server
    log.warning(f"no frontend directory at {FRONTEND_DIR}; serving the API only")


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

# Phase 4 (section 29). Files are named by server-generated id, never by
# anything a caller supplies — see api/library.py for why.
UPLOADS_DIR = Path(__file__).resolve().parent / "uploads"

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

def internal_error(
    context: str, exc: BaseException, code: int = status.HTTP_400_BAD_REQUEST
) -> JSONResponse:
    """An unexpected failure, reported without handing out the details.

    OI-5. Five handlers caught bare `Exception` and returned
    `f"...: {str(e)}"` straight to the caller. What that actually returns
    depends on which exception fired: a SQLAlchemy error carries the failing
    SQL and often the connection string, a file error carries an absolute
    path, an httpx error carries the upstream URL. None of it is useful to
    the reader and all of it maps the inside of the box for anyone else.

    The text is kept in development, because that is where someone is
    reading it, and dropped otherwise. Either way the full traceback goes to
    the log — the point is to move the detail, not to lose it.

    The status code is deliberately unchanged from what these handlers
    already returned. Several of them answer 400 for what is plainly a
    server-side failure; that is wrong, and it is a separate contract change
    from this one.
    """
    log.exception(f"{context}: {exc}")
    return error_response(f"{context}: {exc}" if config.DEBUG else context, code)

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

    # OI-5. This used to read `host="0.0.0.0", reload=True` unconditionally:
    # every interface on the network, with the auto-reloader — which watches
    # the source tree and re-executes it — left on. Neither was a decision;
    # they were the defaults someone typed once.
    #
    # The host now defaults to loopback and has to be widened on purpose, and
    # reload is off in production. `workers` is deliberately absent: see
    # `lifespan.py` for why this app is single-worker.
    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=config.DEBUG and not config.IS_PRODUCTION,
    )