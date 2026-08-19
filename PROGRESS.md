# DigiKitab — Progress & Open Items

Running log of decisions, deferrals, and things that need a later look.
Full evidence for every `F-` reference is in [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md).

---

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 — Audit | **Complete** (2026-08-19) | 20 findings. No production code modified (§73). |
| 1 — Foundation | **PR 1 merged** 2026-08-19 | Closes F-01…F-06, F-08, F-09, F-16, F-21, F-23. |
| 1b — Persistence | **PR 2 open** on `pr/2-persistence` | Postgres, Alembic, auth. Closes F-07. |
| 2 — Content Enrichment | Not started | Google Books key received. Critical path — see F-15. |

### PR 1 — `pr/1-foundation` (awaiting review)

| Commit | Scope | Closes |
|---|---|---|
| 1 | `git init` **with** `.gitignore` — secret containment before any source is tracked | — |
| — | baseline import of the codebase unmodified, so audit claims stay verifiable | — |
| 2 | arbitrary file overwrite, path traversal, concurrent-generation corruption | **F-04, F-05, F-06** |
| 3 | `package.json` + lockfile, pinned deps, real catalogue loads, `/health` honesty | **F-01, F-02, F-03**, F-20 (partial) |
| 4 | delete 986 dead LOC, unshadow `filter-options` | **F-08, F-09, F-16** |
| 5 | README, tests, docs | §62, §71 |

Verified: 30 tests pass; the catalogue resolves to 29,975 real records;
`{"output_file": "../server.js"}` is rejected at both the schema and the
path-derivation layer.

### Boot verification — 2026-08-19, PASSED

Dependencies installed and the app booted end to end. Two blocking defects
found and fixed in the process (commit 6).

| Check | Result |
|---|---|
| `import engine` | OK (4.8s) |
| ML engine fits | OK — 29,975 × 57 feature matrix, k=9, all 6 sub-engines ready |
| `/health` | `ok:true`, `books_loaded:29975`, `data_source:"json"`, `ml_ready:true` |
| 14 live endpoints | all 200 |
| `/api/books/filter-options` | 200 — F-16 confirmed fixed live |
| F-04 exploit over HTTP | 4/4 rejected 422, `server.js` md5 unchanged |
| Recommendations | real titles (Le Queux, Hawthorne, MacDonald) — no synthetic |
| Questionnaire (dark/fantasy) | Le Fanu, Poe, Lovecraft — coherent |
| Test suite | **51 passed, 1 xfailed** |

Installed: scikit-learn 1.9.0, pandas 3.0.5, numpy 2.4.6, gtts 2.5.4, plyer 2.1.0.

> **Install side effect, repaired.** The `uvicorn==0.51.0` pin caused pip to
> downgrade `click` 8.4.2 → 8.1.8, breaking an unrelated `huggingface-hub` in
> the global interpreter. `click` was restored to 8.4.2; uvicorn and the app
> are unaffected. **Recommend a virtualenv** before further installs — this
> project shares a global Python with other work.

Two audit predictions confirmed empirically:
- **F-13** — the LTR model logs `val R2: 1.0000`. Exactly the circular-target
  artefact predicted. The metric is meaningless.
- **F-15** — the 0% description rate is not merely a quality problem; it
  crashed the ML fit (see F-23 below).

---

## Decisions taken

**2026-08-19 · Target market: English only.**
Persian and multilingual deferred to §5.18. Architecture is built **i18n-ready** so the later phase is additive:
`language`/`locale` are first-class columns from the first migration; no hardcoded user-facing strings; `TEXT` + UTF-8 throughout; `NormalizedBook` carries `language` even though only `en` is consumed.
Explicitly **not** built now (§8): RTL layout, locale routing, translation pipeline, per-locale TTS voices.

**2026-08-19 · `backend/static/` stays.** Not deleted. See open item OI-1.

---

## Open items — need review

### OI-1 · `backend/static/` — unmounted second frontend
**Status:** deferred by product owner, 2026-08-19. Do not delete.
**What it is:** a complete, self-contained frontend — `index.html` (116 LOC), `app.js` (135 LOC), `styles.css`. `main.py` never calls `StaticFiles` or `mount`, so it is unreachable at runtime (verified repo-wide).
**Why it matters:** it may be a newer or cleaner design than `frontend/`, which currently carries 834 lines of dead JS (F-08) and duplicated inline `<script>` logic. If `backend/static/` is the better base, the Phase 7 frontend decision (§66) changes materially.
**Risk while deferred:** none. It is inert — not served, not imported, no security surface.
**Review by:** before Phase 7 frontend work (§66). Question to answer: *is this the intended replacement for `frontend/`, an abandoned experiment, or a third-party template?*

### OI-2 · Italian and unlabelled catalogue records — RESOLVED
**Decision (2026-08-19):** filter Italian at **query time**, do not delete.
6,328 `it` and 9,842 `null`-language records out of 29,975. Records stay in the catalogue and remain enrichable later; the `language` column carries the filter. Phase 2 enrichment targets the 13,521 `en` records first, then runs detection on the unlabelled set to recover more English.
**Implementation:** Phase 1 (the `language` column already lands in the first migration as part of the i18n-ready decision).

### OI-3 · Deployment target
Undecided. Determines Postgres, Redis, and object-storage choices. Needed before Phase 1 infrastructure lands.

### OI-4 · Copyright posture (§11)
Confirm in-app reading is restricted to public-domain (Gutenberg) text.

### PR 2 — `pr/2-persistence` (in progress)

| Commit | Scope | Closes |
|---|---|---|
| 1 | docker-compose (pg16+pgvector, redis), config.py, models, Alembic | schema foundation |
| 2 | catalogue ingest with stable (source, external_id) identity | F-10, F-25 found |
| 3 | argon2id + JWT auth, ownership on comments | **F-07** |

Verified against live Postgres 16.15 / pgvector 0.8.6: 8 tables, migration
round-trips, 28,399 books ingested idempotently, full F-07 exploit matrix
rejected. 75 tests pass, 1 xfail.

**Deliberate deferrals in PR 2** — each is a judgement call, not an omission:
- **F-12 (in-memory state) is NOT migrated.** Comments, progress, reminders
  and bandit rewards still live in Python dicts. The tables exist and are
  ready, but moving them rewires `CommentEngine`'s `comment_score` feedback
  into the recommender's DataFrame — a behaviour-affecting change to the ML
  engine, which §4 says to approach with regression coverage first. PR 3.
- **`GET /api/profile/{user_id}` is still unprotected.** Closing the delete
  hole was the urgent half of F-07; the read-authz sweep across remaining
  endpoints is PR 3.
- **Token revocation.** JWTs are stateless with no denylist, so logout cannot
  invalidate a live token. Acceptable now; revisit when Redis is load-bearing.

### F-25 · 1,576 duplicate ids in the catalogue file — MITIGATED
29,975 records collapse to 28,399 unique books; 5.3% are exact-id
duplicates, each appearing precisely twice. Invisible before PR 2 because the
old loader keyed on array position, so every duplicate became its own book —
inflating the catalogue and letting the recommender surface the same title
twice. De-duplicated at ingest (last wins).

**Decision (2026-08-19): provisional.** Keep them de-duplicated for now.
Revisit in Phase 2 once enrichment pulls real ISBNs from Google Books and
Open Library — an ISBN is the evidence that distinguishes a genuinely
separate edition from a true duplicate, and we do not have one today.
Nothing is deleted from the source file, so the decision is reversible.

**Phase 2 action:** after ISBN backfill, re-check the 1,576 collapsed pairs
and split any that carry distinct ISBNs.

### OI-5 · Rate limit required before any deployment — **BLOCKING**
**Status (2026-08-19):** product owner confirmed the app is **local-only until further notice**, so the rate limit was deliberately **excluded from PR 1**.

**This is a deployment gate.** `/api/audiobook/generate` is unauthenticated (F-07) and synchronous (F-19). After PR 1 it can no longer destroy files, but each call still triggers an unbounded fetch-and-synthesise — repeated calls exhaust the server.

**Before the app becomes reachable from any network, ALL of:**
- ~~authentication (F-07)~~ — **done in PR 2**
- per-IP rate limit on `/api/audiobook/generate` — still open
- move generation to a background job (F-19) — still open
- `JWT_SECRET` set in the environment (config refuses to boot without it
  when `ENV=production`)
- read-authz sweep on `GET /api/profile/{user_id}` — still open

Do not deploy, port-forward, expose via tunnel, or demo over a network until these land.

---

## Findings discovered during boot verification

These were not visible to static analysis. All four are pre-existing — none
was introduced by PR 1.

### F-21 · `/health` raised a 500 whenever the ML engine was ready — FIXED
`int(getattr(RECOMMENDER, 'cluster', None))` — but `cluster` is the
`ClusteringModel` object, not a number, so `/health` threw
`TypeError: int() argument must be... not 'ClusteringModel'` on **every call
where the engine had actually fitted**. The endpoint only ever returned 200
while the app was broken.

This is very likely why F-03 survived so long: the one diagnostic that would
have shown `books_loaded: 3000` was itself unusable in the healthy case.
Fixed to read `.best_k`.

### F-22 · The chatbot never returns recommendations — OPEN, PR 2
`ChatbotEngine.respond()` initialises `response["books"] = []` and never
populates it, for any intent. It classifies intent correctly
(`"recommend me a dark thriller"` → `recommend`, confidently) then returns a
canned string and an `action` label. **The "AI chatbot" is an intent
classifier with hardcoded replies and no connection to the recommender.**

Covered by `test_chatbot_returns_recommendations`, marked `xfail(strict=True)`
so it will fail loudly the moment it starts working and the marker can be
removed. Wiring it to `recommend_by_profile` is small — deferred only to keep
PR 1 to verified-safe changes.

### F-23 · pandas 3 / Arrow broke the entire ML engine — FIXED
Under pandas 3, `df["genre"].astype(str).values` returns an
`ArrowStringArray`, not a numpy array. It has no `.flatten()`, so
`FeatureEngineer.fit_transform` raised immediately and `startup()` swallowed
it — leaving every ML feature dead behind what was then a green `/health`.
Fixed at all 7 sites by using `.to_numpy()`.

Follow-on: with the fit progressing further, `fit_comment_embedder` then
crashed on `n_components(16) must be <= n_features(3)` — because all 29,975
descriptions are the same placeholder string, so the TF-IDF vocabulary
collapses to 3 terms (**F-15**). The component count is now clamped to the
available vocabulary, with a warning naming F-15. This keeps the engine alive
on today's data; it does not make those embeddings useful.

### F-26 · 7 of 10 LTR features have zero importance — OPEN, **PHASE 3**

**Measured, not inferred.** Feature importances of the trained model:

```
log_ratings        0.673687
log_ratings_norm   0.313736
avg_rating         0.012577
content_s          0.000000
cf_s               0.000000
cluster_match      0.000000
inv_price          0.000000
mood_match         0.000000
comment_score      0.000000
recency            0.000000
```

**Two causes, both F-13:**

1. *Constant at training time* — `Recommender.fit` passes `diag = np.ones(...)`
   for `content_s` and `cf_s`; `LearningToRank.train` hardcodes `cluster_match`
   and `mood_match` to `np.zeros(...)`; and `comment_score` is 0.0 for every
   book because none has a comment when the model is fitted. A gradient-boosted
   tree never splits on a constant column — then inference feeds all five real
   values.
2. *Circular target* — `relevance = 0.4*(rating/5) + 0.6*norm(log(ratings_count))`
   is a function of three of the model's own inputs, so `inv_price` and
   `recency` carry no signal for it either.

**Two consequences, both material:**

- The LTR component carries the **largest ranking weight (0.32)** and is a
  **pure popularity function**. Content similarity, collaborative filtering,
  clustering, mood, and price contribute nothing to it.
- **The comment feedback loop is fully decorative.** Boosting a book to the
  maximum `comment_score` of 1.0 does not move its rank by a single position.
  User comments do not influence recommendations at all.

**Discovered:** 2026-08-19, while validating PR 3's golden baselines by
sabotage — a maxed `comment_score` failed to change any ranking, and chasing
why produced the measurement above.

**Deferred to Phase 3 by product owner decision (2026-08-19), with reasons:**
1. It is a ranking-affecting change to the ML engine; PR 3's scope is
   persistence. Mixing them violates §2, one deliberate change at a time.
2. The proper fix belongs with Phase 3's broader recommendation work — real
   behavioural signals from `recommendation_log`, not merely un-constanting
   the inputs — so it gets fixed once and correctly rather than patched twice.

**Do not fix by only replacing the constants.** That would leave the circular
target in place and produce a model that looks trained but still predicts a
function of its own inputs. Phase 3 needs both halves: real features *and* a
target derived from logged behaviour.

**Guarded by** two `xfail(strict=True)` tests in
`tests/test_golden_ranking.py` — `test_ltr_has_no_dead_features` and
`test_comment_score_actually_changes_ranking`. Strict means they fail the
suite the moment they start passing, forcing the markers off when fixed.

**Silver lining for PR 3:** because the comment loop is already disconnected,
migrating comments to Postgres cannot regress ranking through it. The golden
baselines still matter, because Phase 3 reconnects it.

### F-24 · The global Python is shared — RECOMMEND A VIRTUALENV
Installing the pinned requirements downgraded `click` and broke an unrelated
`huggingface-hub`. Repaired, but the project should not be installing into a
shared interpreter. A `.venv` and a note in the README is a five-minute fix
worth doing in PR 2.

---

## Deferred technical debt

Carried deliberately, with the reason. Each has a closing phase.

| ID | Item | Why deferred | Closes in |
|---|---|---|---|
| ~~F-07~~ | ~~No authentication anywhere~~ | **Closed in PR 2** | ✅ |
| F-19 | Synchronous TTS blocks the request | Needs Redis + job queue | Phase 6 |
| F-18 | Server-side desktop notifications (`plyer`) | Needs a real delivery channel + queue | Phase 6 |
| F-13 | LTR trained on constant features, circular target | Needs `recommendation_log` data first | Phase 3 |
| F-26 | 7/10 LTR features zero importance; comment loop decorative | Ranking change — belongs with Phase 3's real-signal work, not a persistence PR | Phase 3 |
| F-14 | "CF" is popularity, not collaborative filtering | Needs real interaction data | Phase 3 |
| F-17 | Book "pages" return placeholder strings | Needs real book text ingestion | Phase 5 |
| F-11 | Unmounted second frontend | Product owner deferred — OI-1 | Phase 7 |
| F-20 | Full ML refit on every boot, no artefact persistence | Partially addressed in PR 1 (`limit` cap) | Phase 1 |

---

## Credentials

| Key | Status | Notes |
|---|---|---|
| `GOOGLE_BOOKS_API_KEY` | **Received** 2026-08-19 | Currently at `D:\final\.env` — **must move to the project root**. Nothing loads env vars yet; `python-dotenv` arrives in PR 1 commit 3. |
| LLM provider | Not requested yet | Phase 2 |
| Embedding provider | Not requested yet | Phase 2 |
| TTS provider | Not requested yet | Phase 6 |
| Object storage | Not requested yet | Phase 6 |
