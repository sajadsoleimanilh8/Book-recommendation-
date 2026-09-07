# Restructure notes

Companion to `RESTRUCTURE-PROMPT.md`. Records the baseline import surface that
the compatibility facades must preserve, and every bug or oddity spotted while
moving code and deliberately **left untouched** — this pass is mechanical
extraction, not repair.

---

## 1. Baseline import surface

Run before any code moved, at commit `7fc3813` plus the uncommitted F-47 work.
These two lists define what `backend/engine.py` and `backend/main.py` must keep
exporting after the split.

### `grep -rn "from engine import\|import engine\|from backend.engine" backend tests`

```
backend/alembic/env.py:12:from sqlalchemy import engine_from_config, pool          <- unrelated (SQLAlchemy)
backend/alembic/env.py:16:# backend/ on the path — this project uses flat modules  <- comment
backend/main.py:42:from engine import (
tests/conftest.py:21:# main.py uses flat imports (`from engine import ...`).      <- comment
tests/conftest.py:57:        from db import engine                                 <- unrelated (db.engine)
tests/conftest.py:94:    from db import engine as test_engine                      <- unrelated (db.engine)
tests/conftest.py:119:        from db import engine                                <- unrelated (db.engine)
tests/test_app_boots.py:22:# main.py uses `from engine import ...`                <- comment
tests/test_auth.py:32:        from db import engine                                <- unrelated (db.engine)
tests/test_golden_ranking.py:189:    from engine import UserProfile
tests/test_golden_ranking.py:213:    from engine import UserProfile
tests/test_golden_ranking.py:228:    from engine import UserProfile
tests/test_golden_ranking.py:372:    from engine import UserProfile
```

Real importers of `backend/engine`, and the names they take:

| Importer | Names |
| --- | --- |
| `backend/main.py:42` | `DataLoader`, `GutenbergClient`, `Recommender`, `QuestionerEngine`, `UserProfile` |
| `tests/test_golden_ranking.py` (x4) | `UserProfile` |

Everything else the grep matched is either a comment or `db.engine`, the
SQLAlchemy Engine — an unrelated name collision.

### `grep -rn "from main import\|import main" backend tests`

```
tests/conftest.py:137:    import main
tests/test_app_boots.py:33:    import main
tests/test_app_boots.py:230:    import main
tests/test_auth.py:49:    import main
tests/test_availability.py:30:    from main import price_and_availability
tests/test_availability.py:62:    from main import _ml_to_api, _row_to_book
tests/test_embed_pass.py:106:    import main
tests/test_embed_pass.py:128:    import main
```

Attributes reached through `main` (`grep -rn "main\.[A-Za-z_]*" tests/`):

| Attribute | Used by | Access |
| --- | --- | --- |
| `main.app` | `conftest.py:141`, `test_app_boots.py:36`, `test_auth.py:52` | read |
| `main.RECOMMENDER` | `conftest.py:148,150` | read, **after** startup |
| `main.QUESTIONER` | `conftest.py:156,158` | read, **after** startup |
| `main._SEARCH_ENCODER` | `test_app_boots.py:232`, `test_embed_pass.py:111,113,134,136` | read **and write** |
| `main._SEARCH_ENCODER_ERROR` | `test_app_boots.py:232` | read |
| `main._search_corpus_health` | `test_embed_pass.py:114,137` | call |
| `main.price_and_availability` | `test_availability.py:30` | import |
| `main._ml_to_api`, `main._row_to_book` | `test_availability.py:62` | import |

`main.RECOMMENDER` and `main.QUESTIONER` are read *after* `startup()` has
rebound them, so a plain `from x import RECOMMENDER` re-export would snapshot
`None` and break the fixtures. Live state has to stay reachable as an attribute
of the module object.

---

## 2. Baseline measurements

Taken at `7fc3813` with Postgres up (`docker compose up -d`, port 5433).
Without it the suite still "passes" by skipping wholesale, so every number
below assumes the container is running.

### Route contract — 45 OpenAPI operations

Captured from `main.app.openapi()["paths"]`, which is the real contract. A
plain walk of `app.routes` misses the auth router: FastAPI now nests an
included router behind an `_IncludedRouter` wrapper, so `/api/auth/*` does not
appear as a top-level path route. Both path variants of every dual-registered
route appear below, and both must survive the split.

```
DELETE  /api/comments/{comment_id}
GET     /api/audiobook/jobs/{job_id}
GET     /api/audiobook/{book_id}
GET     /api/audiobook/{book_id}/stream
GET     /api/auth/me
GET     /api/books
GET     /api/books/filter-options
GET     /api/books/{book_id}
GET     /api/books/{book_id}/comments
GET     /api/books/{book_id}/pages
GET     /api/clusters
GET     /api/comments/{book_id}
GET     /api/filter-options
GET     /api/health
GET     /api/profile/{user_id}
GET     /api/progress
GET     /api/reminder/{user_id}
GET     /api/reminders
GET     /api/search
GET     /api/search/semantic
GET     /books
GET     /books/{book_id}
GET     /comments/{book_id}
GET     /health
GET     /reminder/{user_id}
GET     /search
POST    /api/audiobook/generate
POST    /api/auth/login
POST    /api/auth/register
POST    /api/chat
POST    /api/comments
POST    /api/feedback
POST    /api/filter
POST    /api/progress
POST    /api/questionnaire
POST    /api/recommend
POST    /api/reminder
POST    /chatbot
POST    /comments
POST    /feedback
POST    /filter
POST    /progress
POST    /questionnaire
POST    /recommend
POST    /reminder
```

Asymmetries in that list are **pre-existing and preserved exactly**:

- `/chatbot` pairs with `/api/chat`, not `/api/chatbot`.
- `/api/search/semantic`, `/api/clusters`, `/api/profile/{user_id}`,
  `/api/reminders`, `/api/filter-options`, `/api/audiobook/*` and
  `DELETE /api/comments/{comment_id}` are `/api`-only, with no bare alias.
- `/api/books/filter-options` and `/api/filter-options` are two paths onto one
  handler body.

### Test suite

The prompt says 195 tests. The suite has grown since: **267 collected**.

Green baseline for this restructure, taken at `7fc3813` with the Phase A
scaffold in place and Postgres up:

```
$ .venv/Scripts/python.exe -m pytest tests/ -q
264 passed, 3 xfailed, 8 warnings in 279.18s (0:04:39)
```

Every commit from here is measured against that line. A run that reports fewer
than 267 collected has skipped rather than passed — see B-5.

---

## 3. Blockers hit, and what was decided

### 3.1 The tree was red before any code moved. F-47 parked.

The full suite at `7fc3813` **plus the uncommitted working-tree changes** was
`9 failed, 255 passed, 3 xfailed`. Every failure was in
`tests/test_golden_ranking.py`:

```
recommend_fantasy_adventurous rank 1: ranking changed.
    expected: "At the Earth's Core"
    actual:   'Fantasy & Science Fiction'
recommend_no_preferences rank 1 ('Tan Lines'): score drifted 0.8641 -> 0.8678
```

Stashing the uncommitted `engine.py` / `main.py` changes and re-running gave
`18 passed, 2 xfailed`. The cause was the in-flight F-47 language filter, not
the restructure.

That is rule 4 of the prompt: a diff in `/recommend` or `/questionnaire` output
is a bug, not an improvement. `PROGRESS.md` already records why, as **F-48** —
all 6,307 Gutenberg rows are labelled `language = 'it'`, so an English default
excludes the only books in the catalogue with real descriptions and reading
text. `PROGRESS.md` marks F-47 "BUILT, HELD ON F-48".

**Decision:** F-47 was committed to `feature/f-47-language-filter` (`f80ffd1`)
and the restructure proceeds from the green `7fc3813`. Nothing was discarded.
When F-48 is fixed and F-47 re-applied, its hunks land in the restructured tree
as:

| F-47 hunk | New home |
| --- | --- |
| `UserProfile.language` field | `backend/domain/entities.py` |
| language mask in `Recommender.recommend_by_profile` | `backend/services/recommendation.py` |
| `RecommendRequest.language` | `backend/schemas/recommend.py` |
| wiring in the `/recommend` handler | `backend/api/recommend.py` |

`backend/gutendex_language.py`, the gutendex re-derivation pass that unblocks
F-48, went to that branch with it. It is a standalone CLI that nothing imports.
It is therefore **not** on this branch, and `services/enrichment.py` absorbs
`enrich.py` only. When F-47 lands, that pass belongs beside it there.

### 3.2 `backend/db.py` cannot coexist with a `backend/db/` package

Python resolves a package before a same-named module in the same directory, so
creating `backend/db/__init__.py` while `backend/db.py` exists silently
*shadows* `db.py`. Ten modules, `alembic/env.py` and three tests do
`from db import SessionLocal | Base | get_session | engine`, so the shadow
would be catastrophic and quiet.

**Resolution:** `backend/db/__init__.py` *is* the compatibility shim,
re-exporting from `core/db.py`, while `backend/db/models.py` holds the
SQLAlchemy schema. Every existing `from db import ...` keeps resolving. This is
why `db/` is absent from the Phase A scaffold commit — it arrives with its own
move, atomically with the deletion of `db.py`.

### 3.3 Seven tests assert on module *source text*, not on behaviour

These read a `.py` file off disk and string-match it, which pins specific code
to a specific file regardless of where it belongs. The full set — found by
`grep -rn 'BACKEND /' tests/*.py | grep read_text`, after the first two had
already broken a run:

| Test | Reads | What it pins | Retargeted to |
| --- | --- | --- | --- |
| `test_data_loading.py:36` | `main.py` | the `DATA_FILE_CANDIDATES = [` literal | `services/catalogue.py` |
| `test_data_loading.py:66` | `main.py` | `"synthetic"`, `using_real_data` (the `/health` body) | `api/health.py` |
| `test_data_loading.py:77` | `main.py` | `def load_books_raw(limit: Optional[int] = None)` | `services/catalogue.py` |
| `test_security.py:73` | `main.py` | `class AudiobookRequest`, `ConfigDict(extra="forbid")` | `schemas/audiobook.py` |
| `test_security.py:131` | `engine.py` | the `def generate(` body: no `output_file: str`, `AUDIO_DIR` present, no `os.remove(output_file)` | `services/audio.py` |
| `test_security.py:157` | `engine.py` | no `f"_tmp_{i}.mp3"`, `mkdtemp` present | `services/audio.py` |
| `test_security.py:215` | `config.py` | `IS_PRODUCTION`, `JWT_SECRET must be set` | `core/config.py` |

The last one is the reason this table is longer than the five originally
catalogued: it was not found by reading the tests for `main.`-prefixed
attribute access, only by moving `config.py` and watching
`test_production_refuses_a_generated_jwt_secret` fail against its own shim.
The two `engine.py` guards were then found by grep before they could do the
same in Phase C.

These guards are worth keeping — each pins a real security property (F-04's
caller-supplied path, F-06's shared temp file, a per-process JWT secret in
production). What makes them brittle is only that they name a file. Every edit
below changes **which file is read** and nothing about **what is asserted**,
and each is called out in the commit that forces it.

One more couples through the module namespace rather than the file:

| Test | Coupling |
| --- | --- |
| `test_embed_pass.py:111-139` | assigns `main._SEARCH_ENCODER`, then calls `main._search_corpus_health()` and expects the patch to apply — so the two must share a namespace |

Left alone, these cap how much of `main.py` can move and put the ~200-line
target out of reach. **Decision (owner's call):** retarget the five guards at
the modules the code moves to — changing *which file they read*, never *what
they assert*. Each such edit is called out in the commit that forces it.

---

## 4. Bugs and oddities found — left untouched on purpose

### B-1 · `GET /api/books/{book_id}/comments` is a guaranteed 500

`main.py:1819`:

```python
@app.get("/api/books/{book_id}/comments")
def get_book_comments_shortcut(book_id: int):
    return get_comments(book_id)
```

`get_comments` is `(book_id: int, session: SessionDep)`. Called directly like
this rather than through FastAPI, nothing injects the dependency, so it raises
`TypeError: get_comments() missing 1 required positional argument: 'session'`.
Every request to this route is a 500. Confirmed by inspection:

```
get_comments signature: (book_id: 'int', session: 'SessionDep')
```

No test covers the route, which is why it survives. Left as-is: turning a 500
into a 200 is a behaviour change, not a move.

### B-2 · `engine.py` carries a 40-line interactive `__main__` block

`engine.py:1715` loads the CSVs, fits a `Recommender`, and drops into an
`input()` loop. A facade of ≤40 lines cannot carry it, and it is a CLI rather
than app code, so it moves to `scripts/` with the other one-shot passes instead
of being deleted. Behaviour unchanged; only the invocation path changes.

### B-3 · `/api/filter-options` duplicates `/api/books/filter-options`

Two registered paths onto one body — `filter_options_short()` returns
`filter_options()`. Harmless; both preserved.

### B-4 · `main.py` holds live mutable module state that tests reach into

`BOOKS`, `BOOK_BY_ID`, `BOOK_IDX_BY_PK`, `USER_PROFILES`, `RECOMMENDER`,
`QUESTIONER`, `DF` and `DATA_SOURCE` are module globals rebound by `startup()`.
`conftest.py` reads `main.RECOMMENDER` and `main.QUESTIONER` *after* startup has
run, so a `from x import RECOMMENDER` re-export would snapshot `None` and break
every fixture. Whatever ends up holding this state must stay reachable as a
live attribute of the module the tests name.

### B-5 · The test suite needs Postgres but does not say so

With the container down the suite does not fail — it **hangs**, and then lies.

`conftest._ensure_test_database()` shells out to `alembic upgrade head` and
`python -m ingest` with `capture_output=True` and **no `timeout=`**. When
Postgres is unreachable those subprocesses sit in connection retries, so the
session-scoped autouse fixture blocks before the first test runs, with every
byte of diagnostic output swallowed by the capture. Measured here:

```
$ POSTGRES_DB=digikitab_test python -m alembic upgrade head   # container down
ALEMBIC_EXIT=124        # killed at 120s, no output

$ python -m pytest tests/ -q                                  # container down
(no output, 107 minutes, then 26 dots)
```

Two runs during this work were lost that way, one of them for nearly two
hours, and the second reported `EXIT=1` with an empty log — indistinguishable
at a glance from a real regression. The container had been displaced by another
project's `docker compose`, which is easy to do because nothing in the suite
checks for it.

Worth two small changes, neither made here: a `timeout=` on both
`subprocess.run` calls, and a fail-fast check that Postgres answers before the
suite starts. Until then, **confirm `digikitab-postgres` is up before trusting
any run** — every verification run in this restructure is guarded that way.

### B-6 · `__file__`-derived paths silently re-anchor when a module changes directory

Not a bug in the existing code, but the sharpest hazard in this restructure and
the reason to write it down: five constants are computed from `__file__`, so
moving the module that holds them moves the directory they point at — with no
error, no import failure, and nothing in the test suite that would notice until
audio or the catalogue quietly stopped being found.

| Constant | Currently in | Resolves to | Moves to | Must become |
| --- | --- | --- | --- | --- |
| `BACKEND_DIR` | `config.py:18` | `backend/` | `core/config.py` | `parents[1]` |
| `MODEL_DIR` | `embeddings.py:41` | `backend/models` | `ml/embeddings.py` | `parents[1]` |
| `AudiobookEngine.AUDIO_DIR` | `engine.py:645` | `backend/audio_outputs` | `services/audio.py` | `parents[1]` |
| `DATA_FILE_CANDIDATES`, `CSV_FALLBACKS` | `main.py:194-197` | `backend/` | `services/catalogue.py` | `parents[1]` |
| `AUDIO_DIR` | `main.py:191` | `backend/audio_outputs` | `api/audio.py` | `parents[1]` |

`AudiobookEngine.AUDIO_DIR` is the dangerous one: `main.py:191` computes the
same directory independently and the comment there says *"Must match
AudiobookEngine.AUDIO_DIR in engine.py"*. If only one of the two is
re-anchored, generation writes to `backend/services/audio_outputs` while
`/api/audiobook/{id}/stream` reads `backend/audio_outputs`, every generation
reports success, and every stream 404s. `tests/test_security.py` pins the
directory to `BACKEND / "audio_outputs"`.

Re-anchoring is an import fix, not a logic change — the resolved path is
identical before and after — but it is not optional and it is invisible.

### B-7 · The `*_pass.py` scripts are imported by tests, not only run as CLIs

The prompt's Phase B step 5 says to move them to `scripts/` because "these are
not imported by the app — verify with grep". True of the app; not true of the
suite:

```
tests/test_book_vectors.py:23:import book_vector_pass
tests/test_chunk_pass.py:27:from chunk_pass import chunk_one
tests/test_embed_pass.py:34,66,83:import embed_pass
tests/test_front_matter.py:79:import chunk_pass
```

Moving them off `sys.path` breaks four test modules. Handled by adding
`scripts/` to the `sys.path` set up in `conftest.py`, alongside `backend/` —
which the prompt sanctions ("Update conftest.py only if strictly needed"). No
test module's own imports change.

### B-8 · `_SEARCH_ENCODER` is patched through `main` by three test modules, not one

Beyond the source-text guards in §3.3, the search encoder's module state is
reached through `main` from:

```
tests/test_app_boots.py:232       reads main._SEARCH_ENCODER / _SEARCH_ENCODER_ERROR
tests/test_embed_pass.py:111-139  writes main._SEARCH_ENCODER, calls main._search_corpus_health()
tests/test_search_endpoint.py:46  writes main._SEARCH_ENCODER, then drives /api/search/semantic over HTTP
```

The last is the binding one: the patch has to be visible to the *request
handler*, so the encoder state and every reader of it must end up in one
module, and the three tests must name that module. Same class of coupling as
§3.3 and handled the same way — retarget which module they patch, never what
they assert.

---

## 5. Two mechanisms this restructure relies on

Neither changes behaviour. Both exist so that call sites and tests keep
reaching the same objects they reach today, and both are written down here
because they are the parts a reader would otherwise have to reverse-engineer.

### 5.1 Shims alias the module object; they do not re-export names

Every module moved in Phase B leaves a shim at its old path shaped like this:

```python
import sys

from core import ratelimit as _real

sys.modules[__name__] = _real
```

so that `sys.modules["ratelimit"] is sys.modules["core.ratelimit"]`.

The obvious alternative, `from core.ratelimit import *`, is wrong here for
three reasons, all of them load-bearing in this suite:

1. It copies only public names. `tests/test_ratelimit.py:37` patches
   `ratelimit._get_redis`, which a star-import would not have brought across.
2. It creates a *second* namespace. `monkeypatch.setattr(events, "SessionLocal",
   explode)` (`tests/test_events.py:88`) would patch the copy while
   `core.events` kept reading its own — the patch would silently do nothing and
   the test would fail for a reason that looks unrelated.
3. Module-attribute reads like `ratelimit.time` and `net_cache.time`
   (`tests/test_ratelimit.py:65`, `tests/test_net_cache.py:71`) depend on the
   module object itself, not on its exported names.

The `providers/` package additionally aliases each submodule, because
`tests/test_providers.py:292` patches by dotted string —
`monkeypatch.setattr("providers.base.time.sleep", ...)`. Without submodule
aliases, `providers.base` and `services.providers.base` would be two distinct
module objects and the patch would apply to the one nothing imports.

### 5.2 `main.py` keeps its attribute surface with a module-level `__getattr__`

The app's mutable state (B-4) moves out of `main.py`, but the suite reaches it
*through* `main` — and does so after `startup()` has rebound it:

```
conftest.py:148,156          main.RECOMMENDER, main.QUESTIONER
test_audiobook_jobs.py:40,43 main_module.ratelimit, main_module.jobs
test_audiobook_jobs.py:48    main_module.RECOMMENDER.audiobook
```

A plain `from state import RECOMMENDER` re-export cannot work: it binds the
value at import time, which is `None`, and never sees the rebind. So `main.py`
carries a PEP 562 module `__getattr__` that resolves those names live from the
module that now owns them.

This covers reads, which is all the suite does through `main` — with one
exception. `main._SEARCH_ENCODER` is *assigned* by `test_embed_pass.py` and
`test_search_endpoint.py` (B-8), and Python offers no module-level
`__setattr__` hook, so such a write would land on `main`'s own namespace and be
invisible to the handler reading it elsewhere. Those two assignments are
retargeted at the module that owns the encoder instead — the one test-side
change this mechanism cannot absorb.

### B-9 · A test run whose database disappears reports failures, not an error

While verifying the Phase A scaffold, `digikitab-postgres` was stopped by
something outside this work. The run did not abort. It carried on for
**2:00:57** and reported:

```
13 failed, 170 passed, 81 skipped, 3 xfailed
```

Against a baseline of `255 passed, 3 xfailed` on the same code. Nothing in that
output names the cause; the 81 skips and the two-hour wall time are the only
tells, and both are easy to read as "the suite is just slow".

This matters beyond the inconvenience: a restructure verified against a run
like that would look green enough to keep going while actually testing very
little. Every verification run in this work is therefore guarded — the Postgres
container's liveness is polled alongside the run, and a run that loses its
database is discarded and repeated rather than interpreted.

Related to B-5. Together they argue for the suite asserting its database is
reachable at session start and erroring out if not, rather than degrading to
skips — but that is a test-infrastructure change, not a restructure, so it is
recorded here rather than made.
