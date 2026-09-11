# Task: Restructure the DigiKitab backend into a modular skeleton — mechanical extraction only, zero behaviour change

You are a senior backend engineer. The DigiKitab codebase works and placed 3rd of ~200 teams.
Two files carry almost everything and have to be broken apart:

- `backend/main.py` — **1848 lines**: app creation, middleware, ~35 route handlers, all Pydantic
  request models, startup wiring, dozens of helper functions.
- `backend/engine.py` — **1755 lines**: the entire ML system — `DataLoader`, `FeatureEngineer`,
  `ClusteringModel`, `SimilarityEngine`, `CollaborativeFilter`, `LearningToRank`, `ContextualBandit`,
  `GutenbergClient`, `AudiobookEngine`, `CommentEngine`, `ChatbotEngine`, `ReminderEngine`,
  `Recommender`, `QuestionerEngine`, plus `UserProfile` / `Comment` / `Reminder` dataclasses.

The rest of `backend/` is 22 flat modules with no package layering.

This task is **restructuring only**. You are moving code, not redesigning it.

---

## 1. Absolute rules

1. **No behaviour change.** Every route keeps its exact path, method, status codes, request
   schema and response shape. Both the bare path and the `/api/...` alias stay (`/recommend`
   **and** `/api/recommend`, etc.).
2. **Move code verbatim.** Cut a class or function, paste it into its new module unchanged.
   Fix imports. Do not rename, "clean up", re-type, merge or split any function body. If you
   see a bug, write it in `RESTRUCTURE-NOTES.md` — do not fix it here.
3. **`engine.py`'s ranking architecture is preserved, not rewritten** (README rule). The weights
   `{"content":0.28,"cf":0.24,"ltr":0.32,"bandit":0.16}`, the fusion order, the LTR training
   set — all move untouched.
4. **The questionnaire flow is behaviour-frozen.** `tests/golden/` pins its output. Any diff in
   `/questionnaire` or `/recommend` output is a bug, not an improvement.
5. **Tests stay green the entire time.** `python -m pytest tests/ -q` (195 tests) must pass
   after *every* commit, not just at the end. If a step can't keep them green, stop and report.
6. **One bounded move per commit.** Never combine two domain extractions in one commit.
7. **Backward-compatible imports.** `backend/` is on `sys.path` and modules import each other
   flat (`from engine import Recommender`, `import config`, `import store`). After each move,
   the old import path must still work via a re-export shim until the final cleanup commit.
8. Do not touch `frontend/`, `backend/static/`, `alembic/`, `tests/` logic, `server.js`,
   `ingest.py`'s external behaviour, or `docker-compose.yml`.

---

## 2. Target layout

Adapt names to what you actually find, but aim for this. Align module boundaries with
`prmpt.md` §58 (`auth, books, search, recommendation, library, reading, ai, audio, analytics`).

```
backend/
├── main.py                 # thin: create_app(), CORS, middleware, include_router(...), lifespan. ~150 lines
├── lifespan.py             # startup()/shutdown bodies lifted out of main.py (fit ML, build indexes, rehydrate)
│
├── core/                   # framework-agnostic infrastructure
│   ├── config.py           # <- config.py
│   ├── db.py               # <- db.py
│   ├── logging.py          # logging.basicConfig + request-id helpers currently inline in main.py
│   ├── ratelimit.py        # <- ratelimit.py
│   ├── net_cache.py        # <- net_cache.py
│   ├── events.py           # <- events.py   (behavioural event logging)
│   └── jobs.py             # <- jobs.py     (in-process job queue)
│
├── db/
│   └── models.py           # <- models.py   (SQLAlchemy schema; keep as one module for now)
│
├── domain/
│   └── entities.py         # <- UserProfile, Comment, Reminder dataclasses from engine.py
│
├── schemas/                # Pydantic request models currently defined in main.py
│   ├── books.py  search.py  recommend.py  questionnaire.py
│   ├── feedback.py  chat.py  comments.py  reading.py  audiobook.py
│
├── api/                    # FastAPI routers — one module per domain, dual-path preserved
│   ├── deps.py             # <- CurrentUser, OptionalUser, SessionDep re-exported from auth.py
│   ├── health.py           # /health, /api/health, corpus-health helper
│   ├── books.py            # /books, /books/{id}, filter-options, /books/{id}/pages
│   ├── search.py           # /search, /api/search/semantic
│   ├── recommend.py        # /filter, /recommend
│   ├── questionnaire.py    # /questionnaire
│   ├── feedback.py         # /feedback
│   ├── chat.py             # /chatbot, /api/chat
│   ├── audio.py            # /api/audiobook/*  (info, generate, jobs, stream)
│   ├── comments.py         # /comments*, DELETE /api/comments/{id}
│   ├── reading.py          # /reminder*, /progress*, /api/reminders
│   ├── profile.py          # /api/profile/{user_id}
│   ├── clusters.py         # /api/clusters
│   └── auth.py             # <- routes_auth.py router
│
├── ml/                     # pure model components from engine.py (no FastAPI, no DB)
│   ├── data_loader.py      # DataLoader
│   ├── features.py         # FeatureEngineer
│   ├── clustering.py       # ClusteringModel
│   ├── similarity.py       # SimilarityEngine
│   ├── collaborative.py    # CollaborativeFilter
│   ├── ranking.py          # LearningToRank
│   ├── bandit.py           # ContextualBandit
│   ├── embeddings.py       # <- embeddings.py
│   └── chunking.py         # <- chunking.py
│
├── services/               # orchestration — the sub-engines and app logic
│   ├── recommendation.py   # Recommender, QuestionerEngine, fuse_and_rank, LTR training set,
│   │                       #   _ml_to_api / _gutenberg_to_api / apply_filters / fit_ml from main.py
│   ├── catalogue.py        # load_books_raw, _row_to_book, _books_to_df, price_and_availability,
│   │                       #   _build_pk_index  (catalogue loading + row->API mapping from main.py)
│   ├── search.py           # <- search.py + eval_search.py + semantic-search glue from main.py
│   ├── comments.py         # CommentEngine + _rehydrate_comments
│   ├── chat.py             # ChatbotEngine
│   ├── audio.py            # AudiobookEngine
│   ├── reading.py          # ReminderEngine + _reminder_payload / _progress_payload
│   ├── enrichment.py       # <- enrich.py + gutendex_language.py
│   ├── store.py            # <- store.py
│   └── providers/          # <- providers/  (already a package — just move it here)
│       └── gutenberg.py    # GutenbergClient from engine.py joins its siblings
│
├── auth.py                 # stays put for now (argon2id, JWT, deps) — only re-exported via api/deps.py
└── engine.py               # BECOMES A FACADE (see §4)

scripts/                    # one-shot / CLI passes — not importable app code
├── book_vector_pass.py  chunk_pass.py  embed_pass.py  gutenberg_pass.py  eval_search.py
```

`ingest.py` stays at `backend/ingest.py` (it's a documented entrypoint: `python -m ingest`).

---

## 3. Sequence (each numbered item = one commit, tests green before and after)

**Phase A — scaffold, no moves**
1. Create the package dirs above with empty `__init__.py`. Add `RESTRUCTURE-NOTES.md`. Commit.

**Phase B — infrastructure (lowest coupling first)**
2. Move `config.py, db.py, ratelimit.py, net_cache.py, events.py, jobs.py` into `core/`.
   Leave a shim at each old path: `from core.config import *  # noqa` (and re-export names
   explicitly if `*` is unsafe). Update `conftest.py` only if strictly needed.
3. Move `models.py -> db/models.py` + shim. Move `store.py -> services/store.py` + shim.
4. Move `providers/ -> services/providers/` + shim package.
5. Move the `*_pass.py` and `eval_search.py` scripts to `scripts/`. Fix any `python -m`
   references in README / PROGRESS.md. (These are not imported by the app — verify with grep.)

**Phase C — split `engine.py` (the careful part)**
6. Extract the dataclasses -> `domain/entities.py`. `engine.py` imports them back.
7. Extract the pure model classes one per commit -> `ml/*.py`. After each, `engine.py` does
   `from ml.features import FeatureEngineer` etc. Run the golden ranking tests each time.
8. Extract `GutenbergClient -> services/providers/gutenberg.py`.
9. Extract the sub-engines one per commit -> `services/{audio,comments,chat,reading}.py`.
10. Extract `Recommender` + `QuestionerEngine` -> `services/recommendation.py`.
11. `engine.py` is now a **facade**: it only re-exports
    `DataLoader, GutenbergClient, Recommender, QuestionerEngine, UserProfile` (plus anything
    else `grep -rn "from engine import\|import engine" backend tests` shows is used).
    Keep this facade — do not force a mass call-site rewrite in this task.

**Phase D — split `main.py`**
12. Move Pydantic request models -> `schemas/*.py` + import them back into `main.py`.
13. Move helper functions to the service they belong to (`services/catalogue.py`,
    `services/recommendation.py`, `services/search.py`, `services/reading.py`).
14. Extract routers one domain per commit: create `api/<domain>.py` with
    `router = APIRouter()`, move the handlers, register both paths
    (`@router.post("/recommend")` + `@router.post("/api/recommend")` — mirror current dual
    registration exactly), and `app.include_router(...)` in `main.py`. Delete the handler
    from `main.py` in the same commit. Order: health, books, search, recommend, questionnaire,
    feedback, chat, comments, reading, profile, clusters, audio, auth.
15. Move `startup()` / `_build_pk_index` / `_rehydrate_comments` bodies -> `lifespan.py`,
    wired via FastAPI lifespan or kept as `@app.on_event` calling into it (keep whichever the
    codebase already uses — don't migrate the event style in this task).
16. `main.py` is now `create_app()` + middleware + router registration + lifespan hook only.

**Phase E — cleanup**
17. Remove the shim files from Phase B/C **only after** `grep` proves nothing imports the old
    path. Update `conftest.py` `sys.path` note and any `--app-dir` / uvicorn target if the
    module path changed (`main:app` should still resolve). Update README "Layout" section and
    `docs/` to match the new tree.

---

## 4. The `engine.py` facade — exact contract

After Phase C, `backend/engine.py` must be importable and expose the **same names** it does
today, so `from engine import (DataLoader, GutenbergClient, Recommender, QuestionerEngine,
UserProfile)` in `main.py`, `conftest.py`, `test_app_boots.py`, `test_audiobook_jobs.py`,
`test_auth.py`, `test_golden_ranking.py` keeps working with **no edit to those files**:

```python
# backend/engine.py  — compatibility facade after restructuring. See RESTRUCTURE-PROMPT.md.
from domain.entities import UserProfile, Comment, Reminder
from ml.data_loader import DataLoader
from services.providers.gutenberg import GutenbergClient
from services.recommendation import Recommender, QuestionerEngine
# ...re-export anything else the grep below finds
__all__ = [...]
```

Before starting, run and paste the output into `RESTRUCTURE-NOTES.md`:
```
grep -rn "from engine import\|import engine\|from backend.engine" backend tests
grep -rn "from main import\|import main"                          backend tests
```
Those two lists define what the facades must keep exporting.

---

## 5. Definition of Done

- [ ] `python -m pytest tests/ -q` — 195 passed, same as baseline. Attach before/after.
- [ ] `python -m uvicorn main:app --app-dir backend --port 8000` boots; `/health` returns
      `{"ok": true, "books_loaded": 29975, ...}` (not `"data_source":"synthetic"`).
- [ ] `git diff --stat` shows moves, not rewrites: the sum of added+removed lines per moved
      block ≈ 2× its size (cut + paste), not more. Flag any file where you changed logic.
- [ ] Every route from the baseline `grep -nE '@(app|router)\.(get|post|delete)' ` list is
      still registered, both path variants. Include a route-count diff (`app.routes`).
- [ ] No module in `ml/` imports `fastapi`, `sqlalchemy`, or `services/`.
- [ ] `main.py` ≤ ~200 lines, `engine.py` is a facade ≤ ~40 lines, no other module > ~600.
- [ ] README "Layout" section and `docs/PHASE-0-AUDIT.md` file map updated to the new tree.
- [ ] `RESTRUCTURE-NOTES.md` lists every bug/oddity spotted and left untouched.

## 6. If something blocks you

Stop and report. Do not "improve" your way around it. Likely snags:
- circular imports between `services/recommendation.py` and `services/audio|comments|chat`
  (the sub-engines take a `Recommender` ref) — solve with a `TYPE_CHECKING` import or by
  passing the instance in, **not** by merging them back together.
- `conftest.py` sets `sys.path` to `backend/`; nested packages need `backend/` to stay the
  import root — keep it, just add `__init__.py` files.
- `main.py` does heavy work at import time (ML fit in `startup`). Keep it in the lifespan
  hook; don't let it run at module import.
