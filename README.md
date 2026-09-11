# DigiKitab

AI-assisted book discovery and reading. Hybrid recommendation engine
(content similarity + item factors + learning-to-rank + contextual bandit)
over a 29,975-book catalogue, with a cold-start questionnaire, chatbot,
comments, reading progress, and audiobook generation.

Placed 3rd of ~200 teams. Currently being evolved from a competition
prototype into a production system — see [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md)
for the full architecture audit and [PROGRESS.md](PROGRESS.md) for current status.

---

## Requirements

- **Python 3.10+**
- **Node.js 18+**

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

# 2. Node frontend host
npm install

# 3. Configuration
cp .env.example .env
# then edit .env — GOOGLE_BOOKS_API_KEY is only needed from Phase 2 onward

# 4. Database schema + catalogue
cd backend && python -m alembic upgrade head && python -m ingest && cd ..
```

`python -m ingest` is idempotent — re-run it any time to refresh metadata.
It loads 28,399 unique books (29,975 records, minus 1,576 duplicate ids).

## Running

Two processes. `npm start` launches Express **and** spawns the backend:

```bash
npm start
```

- Frontend → http://localhost:3000
- Backend API → http://127.0.0.1:8000
- API docs → http://127.0.0.1:8000/docs

To run the backend alone:

```bash
npm run backend
# or: python -m uvicorn main:app --app-dir backend --port 8000 --reload
```

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

## Tests

```bash
python -m pytest tests/ -q
```

The suite is deliberately dependency-light so it runs before the full ML
stack is installed. It pins the PR 1 security fixes and the data-loading
behaviour — see [tests/README.md](tests/README.md).

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
                   reading, profile, clusters, auth — plus middleware.py
                   (the F-38 query-parameter guard). Every served route.
  schemas/         Pydantic request models, one module per domain
  services/        orchestration — recommendation (Recommender +
                   QuestionerEngine + ranking glue), catalogue, search, store,
                   comments, chat, reading, audio, providers/
  ml/              pure model components — data_loader, features, clustering,
                   similarity, collaborative, ranking, bandit, embeddings,
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
  model_artifacts/ fitted LSA model (gitignored, regenerable)
  routes_auth.py, config.py, db.py, models.py, search.py, store.py, …
                   top-level compatibility shims left by the restructure;
                   each aliases its real module. Removed in Phase E once no
                   call site imports the old path.
frontend/        the served UI (plain HTML/CSS/JS)
server.js        Express: static host + spawns uvicorn
scripts/         keepup.sh — periodic enrichment/embedding loop
tests/           regression suite
docs/            architecture audit, archive/
```

The restructure that produced this layout is recorded commit by commit
(`Cleanup 1/6`..`6/6`, then `Phase C1`..`C15`, then `Phase D1`..`D18`) and
in `RESTRUCTURE-PROMPT.md` / `RESTRUCTURE-NOTES.md`.

## Known limitations

Carried deliberately, each tracked in [PROGRESS.md](PROGRESS.md):

- **Reads are public, writes require auth.** Register via
  `POST /api/auth/register`, then send `Authorization: Bearer <token>`.
  Posting and deleting comments is authenticated and ownership-checked
  (F-07, closed in PR 2). `GET /api/profile/{user_id}` is **not** yet
  protected — tracked for PR 3.
- **Logout cannot revoke a token** before it expires. JWTs are stateless and
  there is no denylist yet; revisit when Redis becomes load-bearing.
- **Audiobook generation is synchronous and unauthenticated** (F-19). It is
  no longer destructive, but it is still a resource-exhaustion vector. A rate
  limit is required before any deployment (OI-5).
- **All state is in-process memory** (F-12). Restarting loses every comment,
  reminder and progress record, and the app cannot run more than one worker.
- **Book "pages" return placeholder text** (F-17). There is no real book text
  in the system yet.
- **Every catalogue description is empty** (F-15). The content-based arm of
  the recommender is effectively title + author + genre only. Fixing this is
  the Phase 2 critical path.
- **The learning-to-rank model is trained on constant features against a
  circular target** (F-13). It logs `val R2: 1.0000` on every boot. That
  number is an artefact of the target being a function of its own inputs.
  Do not cite it.
- **The chatbot never returns book recommendations** (F-22). It classifies
  intent correctly, then returns a canned string — `respond()` never
  populates its `books` list. Tracked for PR 2.

## Contributing

Read [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md) first — it explains why
the code looks the way it does and which parts are deliberately preserved.

Two rules from that audit:

1. **`engine.py`'s ranking architecture is preserved, not rewritten.** Fix
   defects inside it.
2. **The questionnaire flow is behaviour-frozen.** It is competition-proven.
   Any diff in its output is a bug.
