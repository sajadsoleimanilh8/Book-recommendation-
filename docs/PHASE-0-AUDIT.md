# Phase 0 — Current Architecture Audit

**Repository:** DigiKitab (`d:\final\final\final`)
**Date:** 2026-08-19
**Governing spec:** `prmpt.md` §1–4, §70, §72
**Method:** static inspection of every source file. The app was **not executed** — it cannot be (see F-01). Every claim below cites a file and line. Nothing here is inferred from filenames.
**Status:** no production code modified, per §73.

> **This document is a dated record of the tree as it stood on 2026-08-19.
> It is deliberately not updated as the code changes.** The file map in A.1
> and every path cited below describe Phase 0, not the current repository —
> several of the files named here have since been fixed, moved or deleted,
> which is the point of keeping the snapshot legible. For the layout as it
> stands now see README's "Layout" section; for the moves that produced it,
> `RESTRUCTURE-PROMPT.md` and `RESTRUCTURE-NOTES.md`; for the deletions,
> the `Cleanup 1/6`..`6/6` commits of 2026-09-08.

---

## A — Current Architecture Analysis

### A.1 What is actually here

```
final/
├── server.js                  111 LOC   Express :3000 — static host + dead /api proxy + spawns uvicorn
├── public.json                          MISNAMED package.json (see F-01)
├── node_modules/              72 pkgs   installed, no lockfile, no manifest
├── backend/
│   ├── main.py                983 LOC   FastAPI :8000 — 40 route registrations
│   ├── engine.py             1496 LOC   the real asset: hybrid ML + 5 sub-engines
│   ├── notebook_adapter.py    133 LOC   DEAD (F-09)
│   ├── notebook_bootstrap.py   17 LOC   DEAD (F-09)
│   ├── run.ipynb                        1 cell: uvicorn.run(). Not an ML notebook.
│   ├── requirements.txt         6 lines unpinned, 3 deps missing (F-02)
│   ├── site_ready_books.json  12 MB     29,975 records, JSONL — NEVER LOADED (F-03)
│   ├── app_state.json                   ORPHANED, from a prior app version (F-10)
│   ├── audio_outputs/         10 files  ALL 0 BYTES (F-06)
│   └── static/                          a second, unmounted frontend (F-11)
└── frontend/
    ├── index.html filter.html user.html chatbot.html Audiobook.html questionnair.html
    ├── js/  filter.js questionnair.js  ← live
    │        Audiobook.js ChatBot.js user.js  ← DEAD, 834 LOC (F-08)
    └── css/styles.css
```

Roughly **7,000 LOC**. Zero tests. Zero README. Zero CI. Zero Dockerfile. Zero `.env`. Zero migrations. Not a git repository.

### A.2 The runtime topology is not what the spec describes

The spec (§1) says: *"Express serves the frontend; `/api/*` is proxied to FastAPI."*

The proxy exists ([server.js:13-35](../server.js#L13-L35)). **Nothing calls it.** Every frontend page hardcodes the FastAPI origin directly:

| File | Line | Value |
|---|---|---|
| `frontend/js/Audiobook.js` | 1 | `http://localhost:8000` |
| `frontend/js/ChatBot.js` | 2 | `http://localhost:8000` |
| `frontend/js/filter.js` | 3 | `http://127.0.0.1:8000` |
| `frontend/js/questionnair.js` | 3 | `http://127.0.0.1:8000` |
| `frontend/js/user.js` | 1 | `http://localhost:8000` |
| `frontend/Audiobook.html` | 274 | `http://localhost:8000` |
| `frontend/chatbot.html` | 413 | `http://localhost:8000` |
| `frontend/questionnair.html` | 368 | `http://localhost:8000` |
| `frontend/user.html` | 134 | `http://127.0.0.1:8000` |

Actual topology:

```
Browser ──(static HTML/CSS/JS)──> Express :3000
   └────(ALL XHR, hardcoded localhost:8000)────> FastAPI :8000
                                                  (CORS allow_origins=["*"])
Express /api proxy ─────────────────────────────> [never invoked]
```

Three consequences:
1. **The app cannot be deployed anywhere.** It works only on the machine that built it. Any host, any teammate, any judge's laptop on a different port = total failure.
2. `allow_origins=["*"]` ([main.py:38-44](../backend/main.py#L38-L44)) exists to paper over this cross-origin split.
3. The `/api` alias on ~20 routes in `main.py` was added to serve a proxy that is not in the request path. It is now pure duplication.

Note the proxy is also **structurally broken independent of being unused**: `app.use('/api', …)` strips the mount prefix, so `req.url` for `GET /api/clusters` is `/clusters`, and the proxy would request `http://127.0.0.1:8000/clusters` — a route that does not exist. Every `/api`-only route would 404 through it. And `await response.json()` ([server.js:30](../server.js#L30)) would throw on the audio `FileResponse`.

### A.3 Assumptions in §1 that were WRONG

§70 requires these be stated explicitly.

| §1 assumption | Reality | Evidence |
|---|---|---|
| "Express serves the frontend; `/api/*` is proxied to FastAPI" | Proxy is dead code; frontend calls FastAPI directly on a hardcoded localhost origin | A.2 |
| "Existing TTS caching may already exist and MUST be inspected before changing" | **No caching exists.** Every request re-downloads the full text and re-synthesizes the whole book | [engine.py:613-668](../backend/engine.py#L613-L668) |
| "Especially protect: existing audio caching" | There is nothing to protect. All 10 files in `audio_outputs/` are **0 bytes** | F-06 |
| "Data: `site_ready_books.json`" (implying it is loaded) | Path resolution misses it. It is never read | F-03 |
| "Data: 4 CSV datasets" | **All four have been removed** from the working tree. Confirmed with product owner: not needed | — |
| "in-memory `USER_PROFILES`" | Correct — and also all comments, reminders, progress, and bandit state | F-12 |
| "`backend/app_state.json`" (implying live state) | Orphaned. No code reads or writes it. Artifact of a prior version with a different ID scheme | F-10 |
| "Existing notebook-related files: `notebook_adapter.py`, `notebook_bootstrap.py`, `run.ipynb`" | First two are dead code targeting a notebook (`1v1 final.ipynb`) that does not exist. `run.ipynb` is a 1-cell uvicorn launcher | F-09 |
| "Frontend consists primarily of plain HTML/CSS/JS" | Correct, but 3 of 5 JS modules are orphaned and the logic lives in inline `<script>` duplicates | F-08 |
| Implied: `backend/main.py` + `frontend/` are the only UI | There is a **second, complete, unmounted frontend** in `backend/static/` | F-11 |

---

## B — Architecture Weaknesses

Classification per §70-B. Ordered by severity.

### Tier 0 — Blocks any deployment or demo

**F-01 · There is no `package.json`.** — `REPLACE`
`public.json` ([public.json](../public.json)) contains a valid npm manifest under the wrong filename. `npm install` and `npm start` both fail. `node_modules/` (72 packages) exists from a prior install; there is no lockfile, so the dependency set is unreproducible. *Why replace:* rename to `package.json`, commit a lockfile. Zero-risk, unblocks everything else.

**F-02 · `requirements.txt` is incomplete and unpinned.** — `REPLACE`
Declared: `fastapi uvicorn pydantic pandas numpy scikit-learn`. Actually imported but **missing**: `requests` ([engine.py:15](../backend/engine.py#L15)), `gtts` ([engine.py:621](../backend/engine.py#L621)), `plyer` ([engine.py:1084](../backend/engine.py#L1084)). No version is pinned on any line, so a fresh install pulls whatever exists that day — including a NumPy 2.x / scikit-learn ABI break. *Why replace:* a build that cannot be reproduced cannot be demoed.

**F-03 · The dataset is never loaded — the app runs on synthetic data.** — `REPLACE`
[main.py:48-52](../backend/main.py#L48-L52) looks for `site_ready_books.json` in exactly two places:
```python
PROJECT_ROOT / "data" / "site_ready_books.json"      # d:\final\final\data\  — does not exist
Path(__file__).parent / "data" / "site_ready_books.json"  # backend\data\  — does not exist
```
The file actually lives at `backend/site_ready_books.json`. Neither candidate directory exists (verified). With the CSV fallbacks ([main.py:54-59](../backend/main.py#L54-L59)) also now absent, `load_books_raw()` returns `[]`, and startup falls through to:
```python
raw_df = DataLoader.load([])   # main.py:381
```
→ `DataLoader._synthetic(3000)` ([engine.py:236-259](../backend/engine.py#L236-L259)) → **3,000 fabricated books titled `"Book 00000"` by `"Author 42"` with descriptions reading `"A compelling Fiction book about discovery."`**

The application in its current state serves fake data end to end. The 29,975 real records sit unread on disk. `/health` compounds this by reporting `"data_source": "json" if DATA_FILE.exists() else "csv_fallback"` ([main.py:419](../backend/main.py#L419)) — it has no `"synthetic"` branch, so it **cannot tell you this is happening.**

*This is the single highest-priority fix in the repository.* One corrected path restores the real catalogue.

### Tier 1 — Security

**F-04 · Arbitrary file overwrite and deletion via the audiobook endpoint.** — `REPLACE`
`AudiobookRequest.output_file` ([main.py:344](../backend/main.py#L344)) is a client-supplied string with no validation, passed to [engine.py:643-650](../backend/engine.py#L643-L650):
```python
if os.path.exists(output_file):
    os.remove(output_file)          # unvalidated client path
with open(output_file, "ab") as out:
```
`POST /api/audiobook/generate {"output_file": "../server.js"}` deletes and overwrites `server.js`. Unauthenticated — there is no auth anywhere (F-07). *Why replace:* remote unauthenticated arbitrary file write. Must be a server-derived path keyed by book ID.

**F-05 · Probable path traversal on the Express static routes.** — `REPLACE`
[server.js:61-67](../server.js#L61-L67):
```js
app.get('/js/:file', (req, res) => res.sendFile(path.join(__dirname,'frontend','js', req.params.file)));
```
`:file` matches one raw URL segment, then Express URL-decodes it. `GET /js/..%2f..%2fserver.js` decodes to `../../server.js`, and `path.join` normalises it outside the intended directory → source disclosure. *High confidence from code reading; confirm with a live request once F-01 makes the server runnable.* Both routes are redundant anyway — `express.static` ([server.js:11](../server.js#L11)) already serves that tree. **Delete them.**

**F-06 · The audio cache does not exist; the stream endpoint ignores its own parameter.** — `REPLACE`
All ten files in `backend/audio_outputs/` are **0 bytes**. Nothing writes to that directory. Meanwhile:
```python
@app.get("/api/audiobook/{book_id}/stream")
def audiobook_stream(book_id: int):
    audio_file = Path("audiobook.mp3")     # main.py:908 — book_id never used
```
Every book streams one global file from the process CWD. Generation writes to CWD too, with temp files named `_tmp_{i}.mp3` ([engine.py:639](../backend/engine.py#L639)) — so two concurrent generations **corrupt each other's output**. And the frontend does not even call this route; [Audiobook.html:443-445](../frontend/Audiobook.html#L443-L445) requests `/audio/{id}`, which does not exist, above a comment reading `// Assuming the API serves audio at /audio/{id} or similar`. *The audiobook feature does not work.*

**F-07 · No authentication or authorization of any kind.** — `REPLACE` (Phase 1)
A repo-wide scan for `api_key|secret|token|authorization|bearer|jwt|oauth|bcrypt` matched **only book titles**. `user_id` is a client-supplied string defaulting to `"guest"` on every endpoint. Anyone can read anyone's profile (`GET /api/profile/{user_id}`), reminders, and progress, and **delete any comment on any book** (`DELETE /api/comments/{book_id}/{comment_index}`, [main.py:753-755](../backend/main.py#L753-L755)) with no ownership check. Combined with `allow_origins=["*"]` + `allow_credentials=True` ([main.py:38-44](../backend/main.py#L38-L44)) — an invalid CORS combination that browsers reject outright when credentials are actually used.

### Tier 2 — AI quality (the part that must be honest)

**F-13 · The Learning-to-Rank model is trained on constants and a circular target.** — `REFACTOR`

This is the most consequential finding for the AI roadmap, because `ltr` carries the **largest weight in the final ranking**: `WEIGHTS = {"content": 0.28, "cf": 0.24, "ltr": 0.32, "bandit": 0.16}` ([engine.py:1087](../backend/engine.py#L1087)).

Three compounding defects:

*1. Four of ten features are constant during training.* [engine.py:1118-1119](../backend/engine.py#L1118-L1119):
```python
diag = np.ones(len(self.df))
self.ltr.train(self.df, diag, diag)     # content_s and cf_s are ALL ONES
```
and inside `train` ([engine.py:507,513](../backend/engine.py#L507-L513)) features 2 (`cluster_match`) and 7 (`mood_match`) are hardcoded `np.zeros(len(df))`. At inference `_features` ([engine.py:466-487](../backend/engine.py#L466-L487)) supplies **real** values for all four. A gradient-boosted tree trained on a constant column never splits on it → **content similarity, CF similarity, cluster affinity, and mood match are silently ignored by the model that dominates ranking.** Classic train/serve skew, in the four features that matter most.

*2. The target is a function of the inputs.* [engine.py:489-494](../backend/engine.py#L489-L494):
```python
relevance = (average_rating/5)*0.4 + normalized_log(ratings_count)*0.6
```
while features 3, 4 and 9 are `average_rating/5`, `log1p(ratings_count)/15`, and `log1p(ratings_count)`. The model is trained to predict a deterministic combination of its own inputs. The logged `LTR val R2` will read near-perfect and **means nothing** — it measures arithmetic recovery, not relevance.

*3. There is no ground truth in the system.* No clicks, no dwell, no purchases, no held-out interactions. Consequence: **no current metric in this codebase measures recommendation quality.** §52 (Evaluation Platform, Phase 8) is not a nice-to-have — without it there is no way to prove any Phase 3 work is an improvement.

**F-14 · "Collaborative filtering" is not collaborative filtering.** — `REFACTOR`
[engine.py:424-431](../backend/engine.py#L424-L431) builds a `genre × item` sparse matrix whose values are `average_rating * log1p(ratings_count)` and runs SVD on it. There are no users and no interactions in the matrix. It computes *"items in the same genre with similar popularity."* That is a content-and-popularity feature wearing a CF label — and with 40% of the catalogue tagged `genre: "Unknown"` (see D.2), one enormous row dominates the factorisation. *Why refactor, not replace:* the code path and its 0.24 weight are working plumbing; what it needs is real interaction data behind it (§54, Data Moat). Rename it honestly in the interim so nobody demos it as CF.

**F-15 · The content signal is thinner than it appears — 100% of descriptions are empty.** — `PRESERVE` code / `REPLACE` data
`FeatureEngineer` vectorises `title + author + genre + description` ([engine.py:329](../backend/engine.py#L329)). But **all 29,975 records carry the literal string `"No description available"`** (measured, 100.0%). A term present in every document has IDF = 0, so it contributes exactly nothing. The real content vector is **title + author + genre only**, and 39.9% of genres are `"Unknown"`.

**This blocks the entire AI roadmap.** Embeddings (§22), semantic search (§27), the AI Librarian (§28), RAG (§31–33) all need text. There is none. Content enrichment is a hard prerequisite for Phase 2, not a Phase 2 task.

### Tier 3 — Structural

**F-08 · 834 lines of dead frontend JavaScript.** — `REPLACE`
`Audiobook.js` (157), `ChatBot.js` (412), `user.js` (265) are loaded by **no page**. Only `filter.html` → `filter.js` and `index.html` → `questionnair.js` have `<script src>` tags; the other four pages use inline `<script>` blocks containing divergent reimplementations of the same features against *different* endpoints. `user.js` calls `/books/{id}/comments`, `/reminders`, `/chat`, `DELETE /reminders/{id}` — **none of which exist** in `main.py`. Maintaining two drifting copies per feature is how the endpoint mismatches in F-16 happened.

**F-09 · The notebook integration layer is entirely dead.** — `REPLACE` (delete)
`notebook_bootstrap.patch_notebook_recommender` is never imported by anything (verified repo-wide). It targets `backend/1v1 final.ipynb`, **which does not exist**, and patches `main_module.notebook_style_recommend`, **which does not exist** either. `notebook_adapter.py` `exec()`s arbitrary function definitions parsed out of a notebook file ([notebook_adapter.py:44-52](../backend/notebook_adapter.py#L44-L52)) — a code-execution path with no caller. 150 LOC of liability. Delete.

**F-10 · `app_state.json` is orphaned — and it is forensic evidence.** — `DEFER`
No code reads or writes it. Its contents reveal a **prior version of this app** with a different data model: `"total_books": 29975`, and `book_id` values like `"5400"`, `"57333"`, `"135"`, `"7kU_AAAAYAAJ"` — real Gutenberg and Google Books IDs. Those Gutenberg IDs **match the filenames in `audio_outputs/`** (`5400.mp3`, `57333.mp3`, `135.mp3`).

So the "existing TTS caching" §1 refers to was real — in a version that keyed books by stable external IDs and cached audio per book. **That code is no longer in this repository**, and the current code replaced stable IDs with array positions (`"id": i + 1`, [main.py:132](../backend/main.py#L132)). *Recommendation:* keep the file as evidence during migration; the prior ID scheme is the right one and should be restored (see H.1).

**F-11 · A second, unmounted frontend.** — `DEFER`
`backend/static/` holds a complete `index.html` + `app.js` + `styles.css`. `main.py` never calls `StaticFiles` or `mount` (verified). Decide its fate deliberately — it may be a newer design worth keeping — but it is currently unreachable.

**F-12 · All state is in-process memory.** — `REPLACE` (Phase 1)
`USER_PROFILES` ([main.py:64](../backend/main.py#L64)), comments, reminders, progress, and bandit rewards live in Python dicts. Every restart wipes everything. More importantly it **forbids running more than one worker** — two uvicorn processes = two divergent universes. The single hardest constraint on the current architecture, and the direct justification for PostgreSQL in D.

**F-16 · Frontend↔backend contract drift.** — `REFACTOR`
Endpoints called by *live* code that do not exist in `main.py`:

| Caller | Endpoint | Status |
|---|---|---|
| [questionnair.js:72](../frontend/js/questionnair.js#L72) | `GET /api/questionnaire/options` | **404** — never defined |
| [Audiobook.html:445](../frontend/Audiobook.html#L445) | `GET /audio/{id}` | **404** — never defined |
| [main.py:481](../backend/main.py#L481) | `GET /api/books/filter-options` | **unreachable** — shadowed by `/api/books/{book_id}` declared at line 449; FastAPI matches in declaration order and fails `int("filter-options")` → 422 |

**F-17 · The reading feature returns fabricated page content.** — `REPLACE`
[main.py:938](../backend/main.py#L938):
```python
"content": f"page{i+1} از {b.get('title','book')}"
```
Every "page" of every book is that placeholder string (`از` = Persian "of"). There is no book text in the system. Phase 5 (Reading Copilot) has nothing to read.

**F-18 · Server-side desktop notifications.** — `REPLACE`
`ReminderEngine.__init__` starts a daemon thread ([engine.py:976-977](../backend/engine.py#L976-L977)) that polls every 30s and calls `plyer.notification.notify` ([engine.py:1082-1086](../backend/engine.py#L1082-L1086)) — an **OS desktop toast on the server**. On any headless host it raises and is swallowed by a bare `except: pass`. The user never receives anything. This is laptop-demo architecture; it must become a real delivery channel (web push / email) driven by a job queue (§59).

**F-19 · Blocking work in request handlers.** — `REFACTOR`
Audiobook generation is fully synchronous inside the handler: fetch the whole book from Gutenberg, then one gTTS network round-trip per 4,000 characters. A 500KB book is ~125 sequential calls to an **unofficial, rate-limited scrape of Google Translate's TTS endpoint**. It will time out; under demo load it will be throttled. Must move to a background job with a persistent, per-book cache (§59).

**F-20 · Cold start is slow and unbounded.** — `REFACTOR`
Startup parses the dataset, then fits TF-IDF + SVD + MiniBatchKMeans with elbow search over `range(4,15)` + NearestNeighbors + SVD-CF + GBM — all inside the FastAPI startup hook, single-threaded, every boot, with no persistence of fitted artefacts. Meanwhile `server.js` starts Express immediately and never waits for readiness, so early requests hit a 502. Also note `load_books_raw(limit=5000)` ([main.py:374](../backend/main.py#L374)) caps the catalogue at **5,000 of 29,975 books** even once F-03 is fixed.

### What to PRESERVE

Named explicitly, because §4 forbids replacing working subsystems for architectural taste:

- **`engine.py`'s ranking architecture** — the fusion of content-ANN + CF + LTR + contextual bandit with per-user profile feedback is genuinely well-structured and is the reason this placed 3rd. Fix the training defects (F-13/F-14) **inside** it; do not rewrite it.
- **`GutenbergClient`** ([engine.py:562-607](../backend/engine.py#L562-L607)) — a working external provider. It becomes the first implementation behind the §10 provider interface.
- **`QuestionerEngine`** ([engine.py:1246-1443](../backend/engine.py#L1246-L1443)) — the cold-start questionnaire is a real product differentiator and the strongest UX in the repo. Preserve behaviour exactly; only move its persistence.
- **`ContextualBandit`** ([engine.py:534-560](../backend/engine.py#L534-L560)) — correct ε-greedy implementation. It only needs durable reward storage.
- **`CommentEngine`'s sentiment + keyword extraction** — lexicon-based and crude, but working, fast, and free. Keep until an LLM path is proven cheaper per unit of quality.
- **The 29,975-record catalogue** — real Google Books and Goodreads metadata. Its text is missing (F-15), but the identifiers, ratings, and counts are the seed corpus. Do not discard.

---

## C — Proposed Architecture

Adapted to the findings above, not to a template.

### C.1 Target topology

```
                    ┌─────────────────────────────────────────┐
   Browser ────────▶│  FastAPI  (single origin, /api/*)       │
                    │  ─────────────────────────────────────  │
   static assets ◀──│  StaticFiles  ← Express is removed      │
                    └──────────────┬──────────────────────────┘
                                   │  modular monolith (§58)
        ┌──────────┬───────────┬───┴────┬───────────┬──────────┐
     catalog     recsys     library   reading     audio      identity
        │          │           │         │           │           │
        └──────────┴─────┬─────┴─────────┴───────────┴───────────┘
                         │
        ┌────────────────┼─────────────────┬──────────────────┐
   PostgreSQL       Redis            Object store        Providers
   + pgvector    cache/queue         (audio/covers)   ┌───────────────┐
                                                      │ Gutenberg ✔   │
                                                      │ GoogleBooks   │
                                                      │ OpenLibrary   │
                                                      │ LLM / Embed   │
                                                      │ TTS           │
                                                      └───────────────┘
                         │
                    Worker pool (RQ) — enrichment, embeddings, TTS, notifications
```

**Express is deleted, not fixed.** It contributes a dead proxy (A.2), a path-traversal surface (F-05), a fragile `spawn` with `shell:true` whose `kill()` orphans uvicorn on Windows, and a second runtime to deploy. FastAPI's `StaticFiles` replaces it in ~3 lines and collapses the two origins into one — which simultaneously removes the need for `allow_origins=["*"]` and the hardcoded-localhost deployment blocker (A.2). *This is the single largest complexity reduction available.*

### C.2 Module boundaries (§58)

| Module | Owns | Absorbs from today |
|---|---|---|
| `catalog` | books, authors, genres, ingest, enrichment | `DataLoader`, `_row_to_book`, `apply_filters` |
| `recsys` | features, ANN, CF, LTR, bandit, explanations | `FeatureEngineer`…`Recommender`, `QuestionerEngine` |
| `library` | ownership, progress, notes, reminders | `ReminderEngine` state |
| `reading` | book text, chunks, copilot, memory | replaces F-17's placeholder |
| `audio` | TTS jobs, per-book cache, streaming | `AudiobookEngine` |
| `identity` | users, sessions, authz | new (F-07) |
| `providers` | Gutenberg / GoogleBooks / OpenLibrary / LLM / Embed / TTS | `GutenbergClient` |
| `platform` | config, logging, jobs, health, metrics | new |

Rule: modules talk through service functions, never by reaching into each other's tables.

### C.3 Data flow — recommendation

```
POST /api/recommend
  → identity: resolve user (real, not client-supplied string)
  → library:  load profile + interaction history        [Postgres]
  → recsys:   candidates = ANN(taste_vector) ∪ pgvector(semantic) ∪ popular
              scores     = content ⊕ cf ⊕ ltr ⊕ bandit
              select     = bandit.select(ranked, ε)
  → catalog:  hydrate metadata                          [Redis cache]
  → recsys:   attach explanation (§26)
  → observability: log (request, candidates, shown, ranks) → eval corpus (§52)
```

That last line is the point. Today nothing is logged, which is why F-13 has no ground truth. **Logging impressions on day one is what makes Phase 8 possible at all.**

### C.4 Provider boundary (§10)

```python
class BookProvider(Protocol):
    def search(self, q: str, limit: int) -> list[NormalizedBook]: ...
    def get(self, external_id: str) -> NormalizedBook | None: ...
```
One `NormalizedBook` shape; each adapter maps into it. Cross-cutting concerns — timeout, retry with jitter, circuit breaker, rate limit, Redis cache, **and a canned-fixture mode for demos (§12)** — live in a shared decorator, not in each adapter. `GutenbergClient` is refactored in behind this, unchanged in behaviour.

### C.5 Background jobs

Redis + RQ. Not Celery: RQ is ~1 file of config against a Redis instance already required for caching, and nothing here needs Celery's scheduling surface. Queues: `enrichment` (F-15 backfill), `embeddings`, `tts` (F-19), `notifications` (F-18).

---

## D — Technology Decisions

Tied to findings, per §70-D.

**PostgreSQL** — justified by **F-12**, not by preference. In-memory dicts forbid a second worker and lose all state on restart. Postgres additionally supplies the interaction log F-13 needs and the ownership boundaries F-07 needs. SQLite would satisfy persistence but not concurrent writers or pgvector.

**pgvector** — justified by **F-15 + F-03**. 29,975 rows × 768 dims ≈ 90 MB: comfortably inside pgvector's strong range, and far below where a dedicated vector store earns a second datastore, a second consistency model, and a second failure mode. Decisive factor: recommendations must filter by *owned*, *language*, *price* **and** vector distance in one query. Co-locating vectors with relational data makes that one indexed SQL statement instead of an application-side join over two systems.

**Redis** — three concrete jobs: (1) provider-response cache — Gutenberg/Google Books are rate-limited and F-19 hammers them; (2) the RQ broker for F-18/F-19; (3) shared session and bandit state, which is what actually unblocks multi-worker (F-12).

**FastAPI** — **preserve.** It works, the team knows it, the ML stack is Python, and 40 routes already exist. Migrating frameworks would burn Phase 1 for zero product gain — exactly what §4 forbids. The work is *organising* these routes into C.2's modules, not replacing the framework.

**Frontend: keep vanilla, consolidate, defer the framework.** Six pages of plain HTML/JS is not the bottleneck — the bottlenecks are F-03 (fake data), F-07 (no auth), F-08 (dead duplicates), and F-16 (drifted contracts). Phase 1 does exactly two things: **delete the 834 dead lines (F-08)** and **replace the nine hardcoded origins with one relative `/api` base**, which single-handedly makes the app deployable. A React/Next migration (§66) is Phase 7 and must be justified by product need, not taste — per §8, no speculative infrastructure.

**Object storage (S3-compatible)** — justified by **F-06**. Audio currently lands in the process CWD, which is ephemeral on every modern host and unshareable across workers. A generated audiobook is a large, immutable, cacheable blob addressed by a stable key — the exact object-store shape. Local-filesystem driver behind the same interface for dev.

**Job system: RQ** — see C.5.

---

## E — API Strategy

**Gutenberg** — already implemented and working; the first adapter behind the C.4 interface. It is also the **only source of full book text** available today, which makes it the critical dependency for fixing F-17 and F-15.

**Google Books** — the catalogue's origin (16,384 of 29,975 records carry Google volume IDs). Requires an API key (see §72). Primary role: **backfill the 100% missing descriptions (F-15).** This is the highest-value external call in the plan.

**Open Library** — no key, generous limits. Fallback for descriptions, subjects, and covers when Google Books misses; also fills the 35% placeholder covers.

**Normalization** — every adapter emits `NormalizedBook`. Identity resolution is `(source, external_id)` with a title+author fuzzy fallback — necessary because the catalogue currently mixes string Google IDs and integer Goodreads IDs in one `book_id` column (D.2 / F-10).

**Fallback chain** — `local cache → Redis → Google Books → Open Library → Gutenberg → graceful degradation`. Never a hard error to the user; a missing description renders as absent, not as an exception.

**Rate limiting** — token bucket per provider in Redis, sized to the free tier. Backfill runs on the `enrichment` queue at a deliberately low steady rate, not in a burst that trips a quota mid-demo.

**Caching** — provider responses 7 days; book text immutable-forever; **negative results cached 24h** so a missing record does not re-hit the quota on every request.

---

## F — Database Schema

Distinguished per §70-F.

### Core
```
users(id, email, password_hash, locale, created_at)
books(id, external_id, source, title, author, genre, language,
      description, page_count, price, rating, ratings_count,
      published_year, cover_url, created_at)
      UNIQUE(source, external_id)          ← fixes the F-10 ID discontinuity
      INDEX(genre), INDEX(language), INDEX(rating DESC), GIN(to_tsvector(title||author))
user_books(user_id, book_id, acquired_at, source)   PK(user_id, book_id)   ← §21 isolation
reading_progress(user_id, book_id, progress, page, updated_at)   ← replaces F-12 dicts
comments(id, user_id, book_id, text, rating, sentiment, keywords, created_at)
      INDEX(book_id)   ← ownership column is what fixes the F-07 delete hole
reminders(id, user_id, book_id, enabled, last_notified, created_at)
```

### Intelligence
```
book_chunks(id, book_id, ordinal, text, token_count)     ← prerequisite for F-17
book_embeddings(book_id, embedding vector(768))          IVFFLAT (vector_cosine_ops)
chunk_embeddings(chunk_id, embedding vector(768))        IVFFLAT
user_taste(user_id, embedding vector(768), updated_at)
reading_dna(user_id, dimensions jsonb, updated_at)
```

### Observability
```
interaction_events(id, user_id, book_id, event_type, context jsonb, created_at)
      INDEX(user_id, created_at), INDEX(event_type, created_at)
recommendation_log(id, user_id, request jsonb, candidates jsonb,
                   shown jsonb, model_version, latency_ms, created_at)
```
`recommendation_log` is the answer to F-13. Until it exists, no claim about recommendation quality is measurable. **It ships in Phase 1, before the models are touched.**

### Migration order
`users → books → user_books → progress/comments/reminders → chunks → embeddings → observability`
Alembic from the first migration. No hand-edited schemas.

### D.2 — Catalogue quality baseline (measured, 29,975 records)

Recording this now so enrichment progress is measurable against a real starting line:

| Metric | Value |
|---|---|
| Records | 29,975 (JSONL, one object per line) |
| `book_id` string (Google) / integer (Goodreads) | 16,384 / 13,591 — **two ID schemes in one column** |
| **Usable description** | **0 (0.0%)** — all are `"No description available"` |
| `genre = "Unknown"` | 11,968 (39.9%) |
| `page_count = 0` | 10,850 (36.2%) |
| Placeholder cover | 10,449 (34.9%) |
| Language `en` / `null` / `it` | 13,521 / 9,842 / 6,328 |
| Precomputed `cluster` | present but stale — recomputed at every boot, so the stored value is dead weight |

The 6,328 Italian records and 9,842 null languages are a scrape artifact worth a product decision (§72).

---

## G — AI Architecture: where each capability lives

| Capability | Today | Target home | Gate |
|---|---|---|---|
| Classical ML (TF-IDF, SVD, KMeans, ANN) | `engine.py` — works | `recsys/features.py`, fitted artefacts persisted, not refit per boot (F-20) | Phase 1 |
| CF | mislabelled popularity (F-14) | `recsys/cf.py`, rebuilt on real `interaction_events` | **Phase 3, gated on interaction volume** |
| LTR | trained on constants (F-13) | `recsys/ltr.py`, trained on logged impressions | **Phase 3, gated on `recommendation_log`** |
| Bandit | correct, volatile state | `recsys/bandit.py` + Postgres rewards | Phase 1 |
| Embeddings | none | `providers/embeddings.py` → `book_embeddings` | **Phase 2, gated on F-15 enrichment** |
| Vector search | none | pgvector in `recsys` / `reading` | Phase 2 |
| RAG | none | `reading/copilot.py` over `chunk_embeddings` | **Phase 5, gated on real book text (F-17)** |
| LLM | none | `providers/llm.py`, routed cheap→strong (§13) | Phase 2 |
| Model routing | none | `providers/router.py` | Phase 2 |
| Agentic tools | keyword intents in `ChatbotEngine` | `recsys/librarian.py`, tools over catalog+library | Phase 3 |
| OCR | none | `providers/ocr.py` | Phase 10 |
| TTS | gTTS, uncached, unsafe (F-04/F-06/F-19) | `providers/tts.py` + object store + `tts` queue | Phase 6 |

**Read the gate column as the real roadmap.** Four of the marquee AI capabilities are blocked behind two data problems — missing descriptions (F-15) and missing book text (F-17) — not behind model or infrastructure choices. **Content enrichment is the critical path.** Any plan that schedules embeddings before enrichment will produce vectors of the string `"No description available"`.

---

## H — Migration Plan

**H.1 Book identity — do this first, everything depends on it.**
Current `id = i + 1` (array position) is unstable: it changes whenever the dataset is re-sorted, which silently invalidates every stored progress row, comment, and cached audio file. F-10 shows a prior version already used stable external IDs and it was regressed. Restore `UNIQUE(source, external_id)` as the durable key, keep a surrogate integer PK, and expose a stable public ID. Do this **before** any user data accumulates against positional IDs.

**H.2 `Recommender` — preserve, then correct.**
Move behind `recsys.service` with its public shape unchanged. Then, in order: (1) persist fitted artefacts (F-20); (2) fix the LTR train/serve skew by training on real `content_s`/`cf_s` and real cluster/mood values (F-13); (3) start logging impressions; (4) only once logs exist, retrain against observed engagement and retire the circular target. **Steps 1–3 change no ranking behaviour** — they make the correction in step 4 measurable.

**H.3 `GutenbergClient` — preserve behaviour, wrap in the interface.** Same methods, same outputs; gains timeout, retry, cache, and circuit breaker from the shared decorator. Regression test asserts identical results for a fixed query set before and after.

**H.4 Questionnaire — behaviour-frozen.** Golden test first: record current output for a fixed answer set, then refactor only its persistence. Any diff is a bug. This flow is competition-proven; it does not get "improved" during a plumbing migration.

**H.5 TTS — rebuild, do not migrate.** There is nothing to preserve (F-06: zero-byte files, no cache). Build it correctly the first time: server-derived paths keyed by book ID (closing F-04), object storage, background job, real cache, HTTP range streaming.

**H.6 Existing state — `app_state.json` is evidence, not a source.** It is orphaned (F-10) and keyed to the prior ID scheme. Import its `progress` rows only if H.1 lets them map cleanly; otherwise archive it. Do not build a migration path for data no running code produces.

**H.7 Frontend — consolidate before rewriting.** Phase 1: delete the 834 dead lines (F-08), replace nine hardcoded origins with one relative `/api` base, fix the three broken endpoints (F-16). That is a small diff that makes the app deployable. The framework question (§66) waits for Phase 7 and for evidence.

---

## I — Development Roadmap

Only the phases that Phase 0 has enough evidence to specify. Later phases inherit the spec's §68 shape.

### Phase 1 — Foundation *(Tier 1)*

**Objective:** the app is reproducible, deployable, persistent, and serving real data.
**Dependencies:** none. Everything else waits on this.

**Scope:** fix F-01, F-02, F-03; delete Express and mount `StaticFiles`; single-origin `/api`; Postgres + Alembic + the Core schema; auth (F-07); config from environment; structured logging + real `/health`; `recommendation_log` and `interaction_events` writing from day one; delete F-08 and F-09; fix F-16; **pytest with a golden-output test for the questionnaire and the recommender before any refactor**.

**Definition of Done**
- `git clone && cp .env.example .env && docker compose up` serves a working app on a clean machine.
- `/health` reports `books_loaded: 29975` and a `data_source` that can say `"synthetic"` (fixing the F-03 blind spot).
- Restart preserves progress, comments, and reminders.
- Two uvicorn workers behave identically.
- No hardcoded `localhost` remains in `frontend/`.
- Recommendation output is **byte-identical** to pre-migration for a fixed seed set.

**Demo:** clean machine → boot → 29,975 real books → questionnaire → recommendations → restart → state intact.
**Risks:** the golden tests must be written *before* the refactor or F-13's defects get baked in as expected behaviour.
**NOT included:** embeddings, LLM, vector search, new UI, audio.

### Phase 2 — Content Enrichment & Book Intelligence *(Tier 1)*

**Objective:** the catalogue has text. **This is the critical path (see G).**
**Dependencies:** Phase 1; a Google Books API key.

**Scope:** provider adapters (Google Books, Open Library) behind C.4; the `enrichment` queue backfilling descriptions, genres, covers; embedding provider; `book_embeddings` + pgvector; semantic search (§27).

**Definition of Done**
- Usable descriptions ≥ **80%** (from a measured 0.0% — D.2 is the baseline).
- `genre = "Unknown"` below 15% (from 39.9%).
- Semantic search returns relevant results for 20 hand-checked queries.
- Enrichment is resumable and never exceeds provider quota.

**Demo:** search *"books about how habits form"* → semantically correct results with real descriptions.
**Risks:** Google Books quota is the schedule driver; start the backfill early and run it continuously.
**NOT included:** RAG, copilot, audio.

### Phase 3 — Recommendation Correctness *(Tier 1)*

**Objective:** recommendations are honest and measurable.
**Dependencies:** Phase 2; **≥2 weeks of `recommendation_log` data**.

**Scope:** fix F-13 (train on real features and a non-circular target); rebuild CF on real interactions or rename it honestly (F-14); explainable recommendations (§26); offline eval harness (§52 brought forward — you cannot fix ranking without it).

**Definition of Done**
- Zero constant-valued features at training time; a train/serve feature-parity assertion in CI.
- Offline nDCG@10 and CTR@10 computed on held-out logged impressions.
- The new model beats the popularity baseline on held-out data — **or is not shipped.**
- Every recommendation carries a truthful reason string.

**Risks:** the honest outcome may be that popularity is hard to beat at current data volume. Plan for that answer; do not let it be discovered during the demo.
**NOT included:** anything gated on book text.

---

## J — Competition Differentiation

Written honestly, as §70-J requires.

**1. What the 3rd-place version did well.** It shipped a genuinely coherent product: cold-start questionnaire → hybrid recommendations → chat → audiobook → progress, all working, all in one place. Most competitors ship a model in a notebook. The `engine.py` ranking architecture — content-ANN, CF, LTR, and a contextual bandit fused with per-user profile feedback — is more sophisticated than the field, and the questionnaire is a real answer to a real problem. That coherence is why it placed.

**2. What remains technically valuable.** The ranking architecture's *structure* (F-13/F-14 are training bugs inside a sound design, not design failures). `GutenbergClient`. `QuestionerEngine`. The bandit. The 29,975-record catalogue as a seed corpus. The team's demonstrated ability to ship an integrated product rather than a demo.

**3. What must change — stated plainly.** The audit found that the app currently serves **3,000 synthetic books** (F-03); the audiobook feature **does not work** (F-06); the reading feature returns **placeholder strings** (F-17); the LTR model that carries the **largest ranking weight** is trained on constant features against a circular target (F-13); there is **no authentication** (F-07); and it **cannot be deployed off the machine that built it** (A.2). The gap between the demo and the product is larger than the codebase suggests. That is the honest starting position, and naming it is what makes the plan credible.

**4. Why the new architecture is materially stronger.** Not because it uses Postgres and pgvector. Because it makes quality **measurable**: `recommendation_log` and `interaction_events` ship in Phase 1, before any model work, so every subsequent AI claim is backed by held-out data instead of a meaningless R². Today no metric in this codebase measures recommendation quality. After Phase 1, every one does.

**5. What the actual moat is.** Not the models — anyone can call an embedding API. The moat is the **compounding interaction corpus** (§54): logged impressions, dwell, completions, notes, and questions, tied to real book text. That data makes the recommender better every week and cannot be copied by a competitor with a better prompt. It requires the boring Phase 1 infrastructure to exist first, which is precisely why Phase 1 is not deferrable.

**6. Why this is more than "adding AI features."** Because Phase 0 found that four of the five headline AI capabilities are blocked on **data**, not models (G, gate column). A team that "adds AI" ships embeddings of the string `"No description available"` and a RAG copilot over `"page1 از Book 00000"`. Sequencing enrichment before intelligence — and measurement before both — is the difference between a product and a feature list.

---

## §71 — Proposed First Pull Request

**PR: `fix: make the application runnable, reproducible, and serve real data`**

Deliberately the smallest change that removes the blockers preventing all further work. No migrations, no architecture, no new dependencies.

**Committed in this order.** The ordering is part of the deliverable, not incidental: secrets are contained before the first commit exists, then the remote-write hole is closed, and only then does the app get made genuinely runnable. Nothing that increases exposure lands before the exposure is closed.

#### Commit 1 — contain secrets *(must be first; nothing precedes it)*
| # | Change | Fixes |
|---|---|---|
| 1 | `git init` **with** `.gitignore` in the same commit — `node_modules/`, `__pycache__/`, `*.pyc`, `.env`, `*.mp3` | no version control; prevents `GOOGLE_BOOKS_API_KEY` ever entering history |
| 2 | Move `.env` from `D:\final\.env` into the project root; add committed `.env.example` with names only | key is currently outside the repo and unreadable by the app |

*Rationale:* a credential now exists on disk. Once `git init` runs without `.gitignore`, one `git add .` puts it in history permanently. This commit is cheap and irreversible-in-the-right-direction, so it goes first.

#### Commit 2 — close the remote-write hole *(before the app is made runnable)*
| # | Change | Fixes |
|---|---|---|
| 3 | `output_file` no longer accepted from the client. Path is derived server-side as `audio_outputs/{book_id}.mp3`; the field is dropped from `AudiobookRequest`; write confined to that directory and assert-checked after resolution | **F-04 — unauthenticated arbitrary file overwrite/delete** |
| 4 | Temp files move from CWD `_tmp_{i}.mp3` to a per-request `tempfile.mkdtemp()` | **F-06 concurrent-corruption** |
| 5 | Delete the redundant `/css/:file` and `/js/:file` Express routes | **F-05 — path traversal** |
| 6 | Regression test: `POST /api/audiobook/generate {"output_file": "../server.js"}` must 422 and must not touch `server.js` | proves F-04 closed |

*Rationale:* F-04 lets an unauthenticated request delete `server.js`. It is closed **before** the app is made attractive to run (commit 3), so there is never a window where a working, populated app is exposed. Item 5 deletes rather than patches — `express.static` already serves that tree, so the routes are pure surface area.

#### Commit 3 — make it reproducible and serve real data
| # | Change | Fixes |
|---|---|---|
| 7 | `public.json` → `package.json`; commit `package-lock.json` | F-01 |
| 8 | Pin `requirements.txt`; add missing `requests`, `gtts`, `plyer`; add `python-dotenv` | F-02 |
| 9 | Point `DATA_FILE` at `backend/site_ready_books.json`; add a `"synthetic"` branch to `/health` | **F-03** |
| 10 | Raise/remove the `limit=5000` cap | F-20 (partial) |

#### Commit 4 — delete verified-dead code and fix the contract
| # | Change | Fixes |
|---|---|---|
| 11 | Delete `Audiobook.js`, `ChatBot.js`, `user.js` (834 dead LOC) | F-08 |
| 12 | Delete `notebook_adapter.py`, `notebook_bootstrap.py` (150 dead LOC) | F-09 |
| 13 | Move `/api/books/filter-options` above `/api/books/{book_id}` | F-16 |

#### Commit 5 — documentation and the regression net
| # | Change | Fixes |
|---|---|---|
| 14 | `docs/PHASE-0-AUDIT.md` (this file), `PROGRESS.md`, `README.md` with real run instructions | §71 |
| 15 | `pytest` baseline: golden-output tests for questionnaire + recommender, smoke test per live endpoint | §62 |

**Why this and not more.** Commits 1–2 are security and are non-negotiably first. Commit 3's item 9 is the highest-value single line in the codebase. Commit 4 deletes only code proven dead by repo-wide grep. Commit 5 creates the regression net every later phase depends on. Everything structural — Postgres, auth, deleting Express — is **deliberately excluded** so this PR stays reviewable and reversible, per §61 and §71's "do not combine unrelated migrations."

### Residual risk after this PR — stated explicitly

Closing F-04 does **not** make the audiobook endpoint safe, only non-destructive. It remains **unauthenticated** (F-07), and each call still triggers an unbounded synchronous fetch-and-synthesise (F-19). After this PR an attacker can no longer delete files, but can still exhaust the server by repeatedly requesting generation of long books.

That is an accepted, bounded risk for PR 1 — **provided the app is not publicly exposed before PR 2.** Full closure needs auth (F-07) plus the job queue and rate limit (F-19), both of which are PR 2+ and require Postgres and Redis. If the app must be publicly reachable before PR 2, add a blunt per-IP rate limit on `/api/audiobook/generate` to this PR as item 6b.

**Explicitly deferred to PR 2+:** Postgres, Alembic, auth (F-07), removing Express, the provider interface, enrichment.

---

## §72 — Required From the Product Owner

Blocking Phase 1–2. Not requesting credentials that are not yet needed.

### RESOLVED — 2026-08-19

**1. Target market: English only.** Persian and multilingual deferred to a later phase (§5.18). Architecture must be **i18n-ready from the start** so the later phase is additive, not a rewrite. Binding consequences for Phase 1–2:
- `books.language` and `users.locale` are **first-class columns from the first migration**, not added later. Cheapest possible moment to include them.
- No user-facing string is hardcoded in a template. All copy goes through a lookup keyed by locale, with `en` as the only populated catalogue for now. Establishes the seam without the translation cost.
- Text columns are `TEXT` with explicit UTF-8; no `VARCHAR(n)` sized to Latin script.
- Every provider adapter carries a `language` field through `NormalizedBook` even though only `en` is consumed today.
- **Deliberately NOT built now** (§8): RTL layout, locale routing, translation pipeline, per-locale TTS voices. The seam is free; the machinery is not.
- Enrichment (Phase 2) targets the **13,521 `en` records first**, then attempts language detection on the 9,842 unlabelled to recover more English. The 6,328 Italian records are **excluded from the Phase 2 enrichment budget** — see (3).

**2. `backend/static/`: leave in place. Do not delete.** Flagged for later review; recorded in `PROGRESS.md`. Remains unmounted, so it is inert — no runtime or security consequence in PR 1.

### Still open

3. **The 6,328 Italian and 9,842 unlabelled records** — with English-only confirmed, my recommendation is to **exclude Italian from the catalogue at query time** (not delete — filter via `language`) and run detection on the unlabelled set to recover English titles. Confirm, or say if the Italian records should be kept visible.
4. **Deployment target** (Railway / Render / Fly / VPS / other) — determines the Postgres, Redis, and object-storage choices, so it is needed before Phase 1 infrastructure lands.
5. **Copyright posture (§11).** Gutenberg is public domain and safe. Confirm that in-app reading is restricted to public-domain text, or tell me the licensing plan.
6. **Public exposure before PR 2?** If the app will be reachable from the internet before auth lands, I add a per-IP rate limit to PR 1 (see Residual risk, §71).

**Credentials**
7. ~~**Google Books API key**~~ — **RECEIVED** 2026-08-19, `GOOGLE_BOOKS_API_KEY`. Note: the file was placed at `D:\final\.env`, two levels above the project root; it must move to `D:\final\final\final\.env` (PR 1, commit 1, item 2). No code loads env vars yet — `python-dotenv` is added in commit 3.
8. **LLM provider + budget ceiling** — recommend defaulting to the latest Claude models with cheap→strong routing (§13). Needed at Phase 2.
9. **Embedding provider** — bundled with (8), or a local `sentence-transformers` model if cost is the priority. Say which you prefer.

**Needed later (flagging for lead time, not requesting yet)**
9. TTS provider — Phase 6. gTTS is an unofficial scrape and will not survive demo load (F-19).
10. Object storage — Phase 6.
11. Expected traffic and demo date — sizing and rehearsal schedule (§12).
12. Privacy requirements and hosting geography — GDPR changes the `users` schema, so this is cheapest to answer before Phase 1's migration.

**Not requesting:** OAuth credentials, affiliate approvals, payment keys, or a domain. None are needed before Phase 11, and per §8 they are not built speculatively.

---

## Method and honesty notes

- All findings are **static analysis**. The app was never executed, because F-01 makes it unrunnable without modification, and §2.1/§73 forbid modifying code before the audit lands.
- Every finding except **F-05** was confirmed by direct code reading or measurement. F-05 is high-confidence from Express's param-decoding semantics but should be confirmed with a live request once F-01 is fixed.
- The dataset statistics in D.2 were computed over all 29,975 records, not sampled.
- The four CSV datasets referenced in §1 were present at the start of this audit and absent by its end. The product owner confirmed they are not needed, so no recovery was attempted. This is noted only because §1 lists them as ground truth.
- **No production code was modified.** This document is the only artifact of Phase 0.
