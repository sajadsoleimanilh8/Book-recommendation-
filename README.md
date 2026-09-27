# DigiKitab

AI-assisted book discovery and reading. Hybrid recommendation engine
(content similarity + item factors + learning-to-rank + contextual bandit)
over a 29,975-book catalogue, with a cold-start questionnaire, an AI
Librarian (tool-calling chatbot grounded in real search results, including
a reader's own uploaded books), comments, reading progress, and audiobook
generation.

Placed 3rd of ~200 teams. Currently being evolved from a competition
prototype into a production system — see [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md)
for the full architecture audit and [PROGRESS.md](PROGRESS.md) for current status.

---

## Requirements

- **Python 3.10+**

No Node.js dependency — the Express static host (`server.js`, `npm start`)
was retired 2026-09-21 (OI-5). The FastAPI app now serves its own frontend
from one origin; see **Running** below.

## Setup

```bash
# 0a. Start Postgres + Redis (host ports 5433 / 6380, not the defaults —
#     this project shares a machine with other work)
docker compose up -d

# 0. Use a virtualenv — installing the pins into a shared global Python
#    downgraded click and broke an unrelated package once already (F-24).
python -m venv .venv && source .venv/Scripts/activate   # Windows/Git Bash
# python -m venv .venv && source .venv/bin/activate     # macOS/Linux

# 1. Python backend
pip install -r backend/requirements.txt

# 2. Configuration
cp .env.example .env
# then edit .env — GOOGLE_BOOKS_API_KEY is only needed from Phase 2 onward

# 3. Database schema + catalogue
cd backend && python -m alembic upgrade head && python -m ingest && cd ..
```

`python -m ingest` is idempotent — re-run it any time to refresh metadata.
It loads 28,399 unique books (29,975 records, minus 1,576 duplicate ids).

## Running

One process. The app serves its own frontend:

```powershell
.\scripts\run.ps1
```

- App → http://127.0.0.1:8000
- API docs → http://127.0.0.1:8000/docs

The script exists for one reason: it picks the project venv's interpreter.
sentence-transformers is not in the shared global Python (F-24), and starting
from the wrong one does not fail loudly — semantic search returns nothing for
every query instead (OI-9).

Equivalent by hand, if the venv is already active:

```bash
python -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000
```

It binds loopback. Set `HOST` to widen that deliberately — and read
`docs/PROGRESS.md`, OI-5, before exposing this to a network.

> Retired 2026-09-21 (OI-5): this used to be two processes, with `npm start`
> running an Express static host on :3000 that spawned the backend on :8000.
> There is no Node dependency left; `node_modules/` can be deleted.

## Verifying it works

```bash
curl http://127.0.0.1:8000/health
```

Check two fields:

```json
{ "ok": true, "books_loaded": 29975, "data_source": "json", "using_real_data": true }
```

> **`"data_source": "synthetic"` means the catalogue failed to load** and the
> app is serving 3,000 fabricated books (`"Book 00000"` by `"Author 42"`).
> `ok` will be `false`. This state used to be silent — it is the bug the
> `/health` fields above exist to catch. See F-03 in the audit.

Run-by-run enrichment history (not just whether the app is healthy right
now) is at `GET /api/health/enrichment-history`.

## Tests

```bash
python -m pytest tests/ -q
```

550+ tests as of Phase 4, most against a real Postgres instance and the
full ML stack (`docker compose up -d` first) — see
[tests/README.md](tests/README.md) for what still runs without either.

---

## Layout

```
backend/
  main.py          ~165 lines: FastAPI(), CORS, the thirteen include_router
                   calls, the startup hook. No route, no request model.
  engine.py        compatibility facade (~20 lines) — re-exports DataLoader,
                   GutenbergClient, Recommender, QuestionerEngine, UserProfile
                   from where they now live. Was 1,722 lines.
  lifespan.py      startup(): catalogue load, ML fit, pk-index build,
                   comment rehydrate. Plus fit_ml.
  auth.py          argon2id passwords, JWT, FastAPI dependencies
  ingest.py        catalogue -> Postgres (idempotent)
  api/             one router module per domain — health, books, search,
                   recommend, questionnaire, feedback, chat, audio, comments,
                   reading, profile, clusters, auth, library — plus
                   middleware.py (the F-38 query-parameter guard). Every
                   served route.
  schemas/         Pydantic request models, one module per domain
  services/        orchestration — recommendation (Recommender +
                   QuestionerEngine + ranking glue), catalogue, search, store,
                   comments, chat, reading, audio, librarian (the AI
                   Librarian's tool loop), extraction, library_ingest,
                   providers/
  ml/              pure model components — data_loader, features, clustering,
                   similarity, genre_popularity, ranking, bandit, embeddings,
                   chunking. No FastAPI, no SQLAlchemy, no services/ imports.
  domain/          entities.py — UserProfile, Comment, Reminder dataclasses,
                   MOOD_GENRE_MAP
  core/            infrastructure — config, db session, events, jobs,
                   net_cache, ratelimit
  db/              models.py — SQLAlchemy schema (Core + Observability)
  scripts/         one-shot CLI passes, run as `python -m scripts.<name>`;
                   recommend_cli.py is the old engine.py __main__ loop
  alembic/         migrations
  site_ready_books.json   the 29,975-book catalogue (JSONL)
  audio_outputs/   generated audiobooks, one per book_id
  uploads/         private uploaded books (Phase 4, section 29)
  model_artifacts/ fitted LSA model (gitignored, regenerable)
  routes_auth.py, config.py, db.py, models.py, search.py, store.py, …
                   top-level compatibility shims left by the restructure;
                   each aliases its real module. Removed in Phase E once no
                   call site imports the old path.
frontend/        the served UI (plain HTML/CSS/JS)
scripts/         run.ps1 — the launcher (picks the venv interpreter)
                 run_daily_enrichment.ps1 — the standing OI-7 job
                 keepup.sh — periodic enrichment/embedding loop
tests/           regression suite
docs/            architecture audit, archive/
```

The restructure that produced this layout is recorded commit by commit
(`Cleanup 1/6`..`6/6`, then `Phase C1`..`C15`, then `Phase D1`..`D18`) and
in `RESTRUCTURE-PROMPT.md` / `RESTRUCTURE-NOTES.md`.

## Known limitations

Carried deliberately, each tracked in [PROGRESS.md](PROGRESS.md). This
section is a frequent source of drift — several items below replace claims
that were themselves stale (F-12, F-13, F-17, F-19, F-22 were all closed
well before this rewrite; the old text still described the pre-fix state).

- **Not deployed.** Local-only, `127.0.0.1` throughout (OI-3, undecided).
  Two consequences of that: the rate limiter is correct with nothing in
  front of it but not proxy-aware, and is deliberately left that way until
  the deployment shape is decided (F-55); and real collaborative filtering
  has no user-item data to train on yet, since nobody has used the app
  (F-14).
- **Logout cannot revoke a token** before it expires. JWTs are stateless and
  there is no denylist yet; revisit when Redis becomes load-bearing.
- **Single worker only.** The audiobook and library-ingest job registries
  (`core/jobs.py`) are in-process — a job accepted by one worker is
  invisible to another, so `uvicorn --workers 2` is refused at startup
  rather than failing silently later.
- **`GET /api/profile/{user_id}` and comment writes are authenticated**
  (F-07); reads elsewhere are public. Register via
  `POST /api/auth/register`, then send `Authorization: Bearer <token>`.
- **Catalogue descriptions are partial and growing.** Coverage is enrichment
  -bound (Google Books quota, OI-7) — check `/api/health/enrichment-history`
  or `/health`'s `enrichment` field for the current state, not this file.
- **Near-tied ranking scores are not bit-reproducible** across separate ML
  fits (F-51, suspected GPU floating-point non-determinism in the MiniLM
  embedding pass). Golden-baseline tests pin an evidence-based safe prefix
  around it; not fixed at the source.
- **Uploaded books are ingest-only, not readable in-app** (OI-4's
  copyright posture, extended for Phase 4): a reader's own upload is
  extracted, chunked, embedded and searchable by the AI Librarian, but its
  pages are never served back. Only Gutenberg's public-domain text is ever
  rendered as pages.
- **Reading DNA (§25) is not built.** No specification exists for it beyond
  one line in an early planning table (OI-12) — deliberately not guessed
  at, the same mistake the reading-depth target (F-13/F-26) took most of
  Phase 3 to correct once already.

## Contributing

Read [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md) first — it explains why
the code looks the way it does and which parts are deliberately preserved.

Two rules from that audit:

1. **`engine.py`'s ranking architecture is preserved, not rewritten.** Fix
   defects inside it.
2. **The questionnaire flow is behaviour-frozen.** It is competition-proven.
   Any diff in its output is a bug.
