# DigiKitab — Progress & Open Items

Running log of decisions, deferrals, and things that need a later look.
Full evidence for every `F-` reference is in [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md).

---

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 — Audit | **Complete** (2026-08-19) | 20 findings. No production code modified (§73). |
| 1 — Foundation | **PR 1 merged** 2026-08-19 | Closes F-01…F-06, F-08, F-09, F-16, F-21, F-23. |
| 1b — Persistence | **PR 2 merged** 2026-08-19 | Postgres, Alembic, auth. Closes F-07 (write side). |
| 1c — State migration | **PR 3 merged** 2026-08-19 | Golden baselines, F-12, read-authz sweep. |
| 2 — Enrichment | **Code complete; coverage quota-bound** | Passes running. Found F-27…F-30, F-32, F-33. Closed F-17. Coverage is 29.07% descriptions, not code-bound — and **stalled since 2026-09-25**, see F-65. |
| 2b — Book intelligence | **Shipped** 2026-08-29 | §22 BookChunk, §27 semantic search, §18 availability honesty. Found F-34…F-37. |
| S — Structure restructure | **Complete** 2026-09-10, branch `refactor/structure-cleanup` | Cleanup + Phase C (engine.py 1722→22) + Phase D (main.py 1842→165). Zero behaviour change: 264 passed / 3 xfailed and the 9 golden baselines unmoved throughout. Found F-49, F-50, B-10…B-13. |
| 3 — AI recommendation + Librarian | **Shipped**, two deliberate gaps | Librarian tool loop with grounding guard (F-22), reading-depth relevance target (F-26), bandit, book vectors (F-44). **Not built:** §26 explainable recommendations (would need either guessed scope or data that does not exist yet) and Reading DNA (OI-12, no spec). Both are product calls, not backlog. |
| 4 — Personal library | **Shipped** 2026-09-25, except PDF | §21/§22 isolation, §29 upload + extraction (EPUB/TXT), §30 background ingest, §31/§59 private Librarian search. Found F-57…F-61, F-64. **PDF extraction deferred as a later slice** — deferred, not blocked. |
| 5 — AI reading | **In progress** | Shipped: §32 Reading Copilot, §33 AI Book Memory, §38's "summaries" slice, §39's tracking/nudge slice. **Not built:** §37 notes-as-knowledge, the rest of §38 (flashcards, quizzes, spaced repetition) — each needs a product decision, logged where they were assessed. F-26a also closes here and is now due. |
| 6–12 | **Barely started** | Phase 6 has the August audiobook endpoint, now reachable from the UI (F-66 closed 2026-10-02; F-18 still open — no audio has ever been generated, so the engine itself is still unexercised in anger). Phase 7 (dashboard, mood §40) and Phase 8 (evaluation, which owns F-51) not begun. |

**The frontend is the standing gap across Phases 4 and 5.** Every feature
listed as shipped above is backend-only: upload, `/ask`, `/summary`,
`/reading-stats` and semantic search have no UI on any page. Recorded as a
phase-level fact rather than a per-slice note, because it applies to all of
them and will not be fixed by any one slice.

### Where Phase 2 stood (2026-08-29 21:24) — *historical snapshot, not current*

> Kept for the measured provider-yield reasoning below it, which still holds.
> For current coverage read the Phase 2 row above (29.07% descriptions,
> 20,281 pending, stalled since 2026-09-25 — F-65), not these numbers.

```
books             29,975
descriptions       3,146   10.5%      was 1,286 this morning
isbn_13            1,999    6.7%
book_texts         2,446
chunks            35,403   100% embedded, lsa:501a37e8
searchable books   2,705    9.0%
```

Still pending: 9,842 goodreads and 13,016 google_books, both gated on the
Google quota increase (F-31, OI-7). 3,859 gutenberg still queued and moving at
roughly 250 books per hour with a 99.9% success rate since F-32.

**Phase 2 sections status:** §17 done, §18 done (honest `unknown`, no provider
adapters — building them unconfigured would be §8 speculation), §19 done, §22
done, §27 done. The remaining Phase 2 work is coverage, which is quota-bound
rather than code-bound.

### Phase 2 — measured provider yields

The plan changed twice because measurement contradicted my predictions. Both
corrections are recorded because the reasoning generalises.

| Pass | Target | Description yield | Quota |
|---|---|---|---|
| Google + Open Library | modern (Goodreads/Google) | **98.3%** | Google |
| Open Library alone | Gutenberg | **15%** | none |
| **Gutenberg full text** | Gutenberg | **97.5%** | none |

**Correction 1.** I recommended Open Library for the Gutenberg subset on the
grounds that it "shines for older public-domain works". True of edition
coverage and identifiers; **false of descriptions** — it finds those books and
holds no blurb. Normalising titles and authors lifted the match rate (40% →
30% not-found) and did not move descriptions.

**Correction 2.** The right source for Gutenberg books is Gutenberg itself.
The opening prose of the book is better embedding material than a blurb would
have been — the work's own voice rather than ad copy. 97.5% yield, zero quota.

The Open Library pass is still worth running for what it *does* deliver on
that subset: **100% genre** (6,307 books read `Unknown`, and genre is a live
recommender feature), 100% year and page count, 67% ISBN-13.

### F-17 · Fabricated page content — **CLOSED for books we hold text for**
`/api/books/{id}/pages` returned the literal string `"page{n} از {title}"` for
every page of every book. It now paginates real text on paragraph boundaries,
and reports `text_available: false` with a reason where we hold nothing —
an honest empty state rather than invented prose.

`is_complete` is exposed and currently `false` everywhere: the stored text is
the opening chapters (~20 KB/book), not the whole work, and a client must
never present a partial book as complete. Whole-book reading needs a storage
decision (~3 GB), RAG chunking and a paging design — **Phase 5**.

### PR 1 — `pr/1-foundation` (merged)

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
**2026-09-08 · `backend/static/` deleted.** OI-1 resolved; see the entry below.

---

## Waiting on you — 2026-08-31

| # | Decision | Why it is yours | Cost of waiting |
|---|---|---|---|
| ~~1~~ | ~~Google Books quota~~ | **Decided 2026-09-16 (OI-7): run the standing job at the current quota** rather than wait. It accelerates automatically if the increase ever lands. | ✅ |
| ~~2~~ | ~~What is a good recommendation?~~ | **Decided 2026-09-11 (F-26): reading depth.** Shipped, and per-reader attribution followed on 2026-09-17. | ✅ |
| ~~3~~ | ~~`POST /api/feedback` tells users something untrue~~ | **Decided 2026-09-15: remove the claim.** See the entry below. | ✅ |
| ~~4~~ | ~~Branch name~~ | **Decided 2026-09-15: keep `phase-2-enrichment`.** Not worth the rename. | ✅ |
| ~~5~~ | ~~System clock / OI-8~~ | **Handled 2026-09-15** — see OI-8. | ✅ |

Every row in this table is now closed. The live open decision is **OI-12**
(Reading DNA), logged separately below.

*Housekeeping note, because it keeps happening: this table, F-38, F-31 and
F-44 all carried a "still open" or "not yet done" status after the work had
actually landed somewhere else in this document. A stale status is worse than
no status — it sends the next reader to redo something. Reconciled each time
it was found; worth a skim whenever a phase closes.*

### Waiting-on-you #3 · `POST /api/feedback`'s false claim — **FIXED 2026-09-15**

Decided: remove the claim rather than build the taste model it described.
`"message": "Feedback recorded — taste model updated."` and `taste_vector_dim`
both described `UserProfile.taste_vector`, which F-46 already established is
never assigned anywhere in the codebase — the field was always 0.

`api/feedback.py` now returns `"Feedback recorded."` and drops
`taste_vector_dim` entirely, rather than reporting a permanently-zero number
next to a message that no longer claims anything about it.
`GET /api/profile/{user_id}` was checked too and left alone: it already
reports `has_taste_vector` / `taste_vector_dim` as plain, honest state
(`False` / `0`) with no accompanying claim of an update — the same shape of
problem was not present there.

Guarded by `test_feedback_does_not_claim_a_taste_model`
(`tests/test_app_boots.py`), sabotage-checked: reverting the message string
fails exactly that test.

## Structure restructure — S (2026-09-10)

Branch `refactor/structure-cleanup`, 39 commits, each with a full green
suite before the next. Governed by `RESTRUCTURE-PROMPT.md`; every move,
hazard and deviation is in `RESTRUCTURE-NOTES.md`.

**Cleanup (6):** `backend/models/` → `model_artifacts/` (import-shadow
hazard), `app_state.json` archived, three dead files + `backend/static/`
deleted (OI-1 resolved), stale doc invocations fixed.

**Phase C (15):** `engine.py`'s 17 classes extracted — 7 pure model
classes to `ml/`, `GutenbergClient` to `services/providers/`, four
sub-engines to `services/`, `Recommender`+`QuestionerEngine` to
`services/recommendation.py`, the 3 dataclasses to `domain/entities.py`.
`engine.py` is now a 22-line facade. The `__main__` CLI loop moved to
`scripts/recommend_cli.py`.

**Phase D (18):** `main.py` split — 9 request models to `schemas/`,
catalogue and recommendation helpers to `services/`, all 45 routes into
13 `api/*.py` routers, `startup()`+`fit_ml` to `lifespan.py`, the F-38
query-parameter guard to `api/middleware.py`. `main.py` is 165 lines:
`FastAPI()`, CORS, `include_router` ×13, the startup hook, and the two
helpers (`error_response`, `get_profile`) routers reach through it.

**Verified at the checkpoint:** app boots on the real 29,975-book
catalogue (`/health` → `ok:true`, `data_source:"json"`, every engine
ready); 45 OpenAPI operations unchanged; no `ml/` module imports
fastapi/sqlalchemy/services; golden ranking 18 passed / 2 xfailed
throughout. `services/recommendation.py` at 720 lines is over the ~600
soft target — `Recommender`+`QuestionerEngine` cannot be separated
(QuestionerEngine takes a Recommender directly) and the ranking glue was
prompt-directed to live beside them; recorded, not force-split.

**Not done, deliberately:** the ~15 top-level compatibility shims
(`config.py`, `db.py`, `search.py`, …) stay until Phase E, when a grep
proves no call site imports the old flat path. F-48, the recommendation
-target question and the Google quota item were untouched.

## Open items — need review

### OI-1 · `backend/static/` — unmounted second frontend — RESOLVED
**Decision (2026-09-08):** deleted, in `Cleanup 4/6`. Recoverable from history.
**Why it changed:** the reason recorded for deferring — that `frontend/` carried 834 lines of dead JS (F-08) and might be the worse base — stopped being true when PR 1 closed F-08. Re-verified before deletion: never mounted, and `app.js:122` calls `/api/discover`, which is not one of the 45 registered operations.

**Original entry, for the record:**
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

**Now blocks F-55 as well (2026-09-21).** OI-5 closed every defect on its own
list, so the remaining gate is this decision rather than any missing work.
Specifically: whether there is a reverse proxy in front. With one, the rate
limiter needs `--proxy-headers --forwarded-allow-ips=<the proxy's IP>` *and*
a `client_ip()` that reads the header it is then safe to trust; without one,
the current code is already correct. The two are mutually exclusive and
guessing wrong turns the limiter into a shared bucket that locks everybody
out at the first ten requests. Deliberately left unset until this is
answered — nothing is exposed meanwhile, because the app is local-only, which
is the same fact this item exists to change.

### OI-4 · Copyright posture (§11) — **CONFIRMED 2026-09-20**

**In-app reading is restricted to public-domain text only.** Gutenberg and
nothing else; no copyrighted content is ever served for reading, under any
path.

**Extended 2026-09-22 for Phase 4: ingest-only.** A reader may upload a book
they own; the app extracts, chunks, embeds and searches it, and the librarian
answers grounded in it. It does **not** render its pages back. The reader gets
a private reading assistant, not a reader.

That keeps OI-4's original sentence literally true — no non-public-domain text
is served for reading — while section 29's actual value (private RAG over your
own library, section 22) still lands. Legal exposure stays where it was while
the product is early; full in-app reading of uploads is revisitable once it is
not.

The line this draws is narrow and worth stating precisely, because the
temptation later will be to widen it by accident: **extraction and retrieval
are permitted, rendering is not.** A chunk may be embedded, searched, and
quoted back as grounding for an answer. A page may not be served as a page.

---

This ratifies what the code already does rather than changing it — `book_texts`
holds Gutenberg openings alone, `/api/books/{id}/pages` serves from that table
and reports an honest empty state elsewhere (F-17), and `price_and_availability`
already treats Gutenberg as the one verifiably-free source (F-36). What the
confirmation adds is that this is now a **rule, not an accident**: any future
feature that would serve non-public-domain text for reading — a personal
upload rendered back to its owner is the obvious Phase 4 case, and is
different, since that is the reader's own file — needs this decision revisited
first, not a quiet extension of the existing path.

### PR 2 — `pr/2-persistence` (merged)

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

### PR 3 — `pr/3-state-migration` (merged)

| Commit | Scope | Closes |
|---|---|---|
| 1 | golden-output baselines, recorded first | found **F-26** |
| 2 | F-26 logged in full | — |
| 3 | persist comments/progress/reminders; read-authz sweep | **F-12**, F-07 (reads) |

**98 passed, 3 xfailed.** The golden baselines are unchanged after the
migration — that is the evidence it did not move ranking.

**Verified by hard restart** (taskkill, port confirmed free, connection
refused, new PID, zero bind errors): comments, progress, reminders, the
session, and the derived `comment_score` all survive. Posting a second
comment after restart returned 0.5, proving the pre-restart 0.25 was
replayed rather than reset.

> **Testing note worth keeping.** The first restart test was a false pass.
> `pkill` does not kill uvicorn on Windows; the replacement process failed to
> bind with `Errno 10048` and exited, and the *original* process answered the
> queries. It was caught only because a 1-second boot is impossible for a
> 6-second ML fit. When testing restart behaviour, assert the port is free
> and the new PID differs — do not trust the kill.

**API contract change:** `DELETE /api/comments/{book_id}/{comment_index}` is
now `DELETE /api/comments/{comment_id}`. Positional indices are racy once
comments are shared persistent rows — two concurrent deletes shift each
other's target. Ids come back from POST and from GET.

### OI-11 · What should a price filter do when no price is known? — **DECIDED, SHIPPED**

Approved 2026-08-30: do not offer a filter that cannot work.

Investigating it turned up something better than expected — **there was no UI
control to hide.** The questionnaire has six questions and none is about
price, and no frontend file references `max_price`. So the decision landed at
the API instead, where the parameter really does exist.

`price_within(row, cap)` now filters on *known* prices only. Unknown is not
free: a book nobody has priced may well cost more than the cap, and we cannot
claim otherwise. What survives is the Gutenberg subset, which is verifiably
free — narrow, but every result it returns is true.

Shared by both call sites, for the same reason `price_and_availability` is:
the catalogue path and the recommendation path each had their own copy of the
comparison, so they could disagree and only one would ever get fixed.

Sabotage-checked: restoring `_safe_float(list_price) <= cap` fails exactly the
two new tests. `test_no_cap_still_returns_unknown_priced_books` holds the
other side, so the fix cannot overshoot into hiding books nobody asked to
filter.

### F-38 · `/api/books` silently ignores unknown query parameters — **DECIDED AND SHIPPED**

Found while testing OI-11. `GET /api/books?max_price=5` returned **all
29,975 books with HTTP 200**. Not because the filter was broken — because the
route never declared `max_price`, and FastAPI discards undeclared query
parameters without complaint.

Same failure shape as OI-11 itself, one layer up: the caller believes they
filtered, the server says 200, and the data says otherwise. Any client typo
(`ratingmin`, `max_pages`) behaved identically.

**Decided (2026-09-15, reconfirmed): 422 undeclared query parameters.**
Already implemented and live by the time this was reconfirmed — see the
`api/middleware.py` entry further down this document for the full
implementation writeup (per-route resolution, sub-dependency walking, cache
busters tolerated, eight tests). This entry is kept only as the original
discovery record; do not read it as still open.

See F-36. `max_price` currently treats unknown as 0, so "under $5" returns
everything. Excluding unknown-price books is accurate but returns an empty
list until an availability provider exists. My recommendation: hide the price
filter in the UI while `availability` is unknown for the whole catalogue,
rather than offering a control that cannot work.

### OI-10 · Should an invalid token 401, or degrade to anonymous? — **DECIDED, SHIPPED**

Approved 2026-08-30: 401 for malformed/forged, a distinct code for expired so
the frontend can refresh rather than logging the user out.

Shipped in `decode_token_detailed`. Three codes, in the body as
`detail.code` and in a RFC 6750 `WWW-Authenticate` header:

| code | when | client should |
|---|---|---|
| `token_expired` | signature valid, `exp` passed | refresh, retry |
| `token_invalid` | forged, malformed, or unknown user | log in again |
| `account_inactive` | valid token, `is_active = false` | show a message |

`get_current_user_optional` now raises rather than downgrading. "Optional"
means *the endpoint tolerates anonymous visitors*; it never meant
authentication is optional once attempted. A request bearing a token is
claiming an identity, and a bad claim is an error, not an anonymous visit.

Expiry is checked **before** the user row is loaded, so an expired token never
reaches the database. `test_an_expired_token_says_so_distinctly` pins that.

The F-34 `xfail(strict=True)` is now three real tests, including
`test_no_token_is_still_anonymous_not_an_error` so the fix cannot regress into
breaking anonymous browsing.

**Frontend impact, as accepted:** any read endpoint called with a stale token
now 401s instead of quietly returning anonymous results. That is the point —
the old behaviour hid expiry from the user while their private data vanished
from their own results.


See F-34. Current behaviour is silent degradation on every `OptionalUser`
route. The security-correct answer is 401, but it changes behaviour across the
API and the frontend must then handle expiry. My recommendation: 401 for a
*malformed or forged* token, and a distinct, explicit response for an
*expired* one so the frontend can refresh rather than log the user out.

### OI-9 · Install sentence-transformers for real embeddings? — **DECIDED, INSTALLED**

Approved 2026-08-30, virtualenv only. Safety re-checked first, and the risk
was lower than reported: the surviving training process runs from
`D:\SportsStrategyCoachAI\...env\`, i.e. it was **already insulated** from
the shared global interpreter. The project venv makes it moot either way.

`.venv/` holds torch 2.13.0+**cpu**, sentence-transformers 6.0.0. CPU-only was
deliberate: it is ~200 MB rather than ~2.5 GB, and it cannot contend with the
training job for the GPU.

**Measured, non-destructively — same 4,000 chunks, both backends, in memory:**

```
same-book@10     lsa 50.7%  ->  minilm 79.3%    (+28.7 points)
encode 4k        lsa 2s     ->  minilm 44s      (~14 min for the full corpus)
```

Qualitatively, on the query class section 27 asks for:

```
"a terrifying story set in a haunted house"
   lsa     -> The Shoemaker's Apron; Complete Original Short Stories
   minilm  -> The Forsaken Inn; The Heath Hover Mystery
```

Honest caveat: the sample is Gutenberg-only, so a query like "practical advice
for starting a business" has no good answer in it. That is a corpus limit, not
a model one, and it is why the Google-quota records matter.

The venv also runs the full suite green (219 passed), so it is a complete
environment — which incidentally closes **F-24**, the shared global Python.

**Not yet flipped.** `EMBEDDING_BACKEND=minilm` plus a re-embed is the next
step; it requires the app and the keep-up pass to run from `.venv`, which is
an operational change worth doing deliberately rather than as a side effect.

### F-44 · The obvious way to do Phase 3 item #2 would silently break 77% of the catalogue — **CAUGHT IN DESIGN, NOW BUILT**

Plan item #2 was "swap `content_s` from TF-IDF to the MiniLM vectors". Checked
the coverage before writing any of it:

```
books                29,975
books with a chunk    6,989   (23.3%)
books with NO chunk  22,986   (76.7%)
```

Embeddings exist per *chunk*, and chunks only exist for books that have a
description or Gutenberg text. A straight swap would give `content_s = 0` to
**more than three quarters of the catalogue** — and since content carries
weight 0.28 directly plus feeds the LTR component's 0.32, those books would
quietly stop being recommendable on any content basis. Nothing would error.
The rankings would just get worse in a way no test currently asserts against.

**Design decision: one vector per book, same recipe for every book.**
`title + author + genre + description (when present)`, embedded with MiniLM.
Uniform construction means one vector space at 100% coverage rather than two
populations with different character, which is the F-39 mistake wearing a
different hat. Books with descriptions get richer text; the recipe does not
change.

Cost is negligible — 29,975 short strings is seconds on the GPU.

**Open question for the product owner, not blocking:** whether a metadata-only
vector should be *marked* as thinner, so the UI can distinguish "similar
because we read both books" from "similar because both are 1890s adventure
novels". I am not building that distinction yet; noting it because it is
cheap now and expensive to retrofit once explanations (§26) exist.


**Built 2026-08-31.** `book_vectors`, one row per book, new table with a
verified rollback (`downgrade` drops it; round-tripped against the populated
29,975-row database before relying on it — the F-28 lesson).

```
books              29,975
with_vector        29,975    100.0%
with_description    6,998
metadata_only      22,977
by_model           {'minilm': 29975}
skipped_empty           0
```

Seconds on the GPU. Both populations exist and are counted, so the coverage
is demonstrably not accidental.

**Wired into ranking the same day, F-46.** Building the vectors and switching
`content_s` onto them were separate commits on purpose: this one is additive
and changed no behaviour; F-46 is the one that moved every ranking, with the
golden baselines read rather than regenerated.

**Wired into book-level semantic search, 2026-09-18 (F-44 follow-through).**
`GET /api/search/semantic`'s book-level mode (`by_passage=false`, the
default) queried `book_chunks` collapsed to one row per book, same as
passage search — which meant it inherited passage search's coverage limit
(~23% of the catalogue, chunks only) for no reason: nothing about "find a
book like X" needs a passage, only a book-level vector, and one already
existed with 100% coverage. `services/search.py::search_books` now queries
`book_vectors` directly. Verified live against the real catalogue: queries
for "overcoming grief and loss" and "philosophy of science" both returned
five coherent, on-topic books, every one of them `has_description: false` —
i.e. every result came from a book that would have been invisible to the old
chunk-based book search, matched purely on title/author/genre. Guarded by
three new tests in `tests/test_search.py`, sabotage-checked
(`test_book_level_search_finds_a_book_with_no_chunk_at_all` is the one that
proves the coverage claim directly, not just by inspection).

Passage search (`by_passage=true`) is untouched and still `book_chunks`-based
— deliberately: `book_chunks` and `book_vectors` are built from different
text (a real passage vs. `title. author. genre. description`), so their
similarity scores are not comparable and were never merged into one ranked
list (the same principle F-39 already established: comparable vectors need
comparable construction, not merely the same model).

**A real bug in the test fixtures, found and fixed before it could land.**
The first version of the new tests' `book_vectors` fixtures deleted a real
catalogue book's `book_vectors` row in setup and only deleted it again in
teardown — never restoring the original. `book_chunks` fixtures already do
exactly this delete-and-leave-empty pattern safely, because nothing depends
on every chunk existing. `book_vectors` is different: `load_content_vectors`
(`services/store.py`) is all-or-nothing — one missing book_vectors row falls
the *entire* ML fit back to TF-IDF, moving every golden ranking score. Caught
by running the full suite, not just the new tests: `tests/test_golden_ranking
.py` failed 10 ways after the search tests ran and left `digikitab_test`
short two rows. Fixed by snapshotting the original row before deleting it and
restoring it in teardown, and the already-damaged test database was repaired
(`book_vector_pass` regenerated the 2 missing rows) before trusting a green
run again.

### F-48 · All 6,307 Gutenberg books were mislabelled `language='it'` — **FIXED**

Every Gutenberg row in `site_ready_books.json` carries `language='it'`, which
`normalize_language` renders as `"It"`. A function-word check on 600 books
with stored text found 600 English, 0 Italian — the entire Gutenberg slice of
the catalogue was mislabelled Italian. This blocked F-47's English-default
language filter outright: applying it as written would have hidden the only
6,307 books with real descriptions and reading text from every English
reader, and every existing test would still have passed, because none of them
know what language a Gutenberg book is actually in.

**Fixed by asking Gutenberg, not by trusting the 600-book sample.** The
sample was unambiguous, but 600 of 6,307 is still a sample, and Gutenberg does
hold real non-English texts — bulk-setting everything to `en` on the strength
of a sample would have replaced one wrong blanket label with a probably-right
one, the same shape of shortcut that produced the bug. `scripts/
gutendex_language.py` re-derives the true label per book from gutendex
(Project Gutenberg's own API), keyed on the `external_id` every row already
carries. A 640-book live dry run came back 640/640 `it -> en`, matching the
function-word check exactly, before anything was written.

**Result, live, all 6,307 books: 6,307 `en`, 0 remaining, 0 anomalies.**
Every single change across the whole run was `it -> en` — no book resolved to
any other language.

**Two things broke mid-run, both fixed rather than worked around:**

1. The first version of the fetch only read page one of gutendex's paginated
   response (32 results/page), so a batch of 100 silently repaired ~32 and
   miscounted the rest as "not in gutendex" — the same absent-as-negative
   shape as F-29, found by measuring a live response (`count=100,
   page_results=32, next=yes`) rather than by reading the code. Fixed by
   following `next` links, and treating a partial-page failure as a
   whole-batch failure so no partial writes can happen.
2. Docker's engine — not just the containers, the whole daemon — stopped
   completely twice during the live run, and the script had no protection
   against it: one long-lived session for the entire run, no `except` around
   the commit, so `OperationalError` propagated up uncaught and the process
   vanished with nothing to show for it beyond the last flushed log line.
   Fixed by mirroring the F-30 pattern already established in `scripts/
   gutenberg_pass.py` — roll back, log how far it got, stop cleanly rather
   than crash or burn gutendex's rate-limited budget against a database that
   cannot receive the writes — plus a fresh session per batch instead of one
   session for the whole run, and `only_unresolved=True` so a resume only
   re-touches what is actually left. A watchdog wrapper (restart Docker,
   re-invoke, repeat) then finished the remaining 2,115 books unattended
   across two more automatic resumes. Verified by sabotage: swapping the
   `except` clause to the wrong exception type makes the new regression test
   fail exactly as expected.

**A second database needed the same correction, and almost went unnoticed.**
`tests/conftest.py` unconditionally sets `POSTGRES_DB=digikitab_test` at
import time — every `pytest` invocation targets that database regardless of
what `POSTGRES_DB` is set to in the environment. This means the entire test
suite, including every "full suite green" check run earlier in this session,
has only ever exercised `digikitab_test`, never the `digikitab` database the
live repair actually wrote to. The first golden-baseline comparison after the
fix passed with zero drift — not because the fix has no ranking effect, but
because `digikitab_test.books.language` was untouched and still said `'it'`
for all 6,307 rows. Caught by checking `LANGUAGE_OVERLAY`'s own report
(`changed: 0`) rather than trusting a clean run.

Fixed by applying the same, already-100%-verified `it -> en` mapping directly
to `digikitab_test` (confirmed first: identical row count, identical
`external_id` ordering, same seed) — a plain `UPDATE`, not a second gutendex
run, since gutendex had already answered this question conclusively and
re-asking would only spend its rate-limited capacity to reconfirm a known
fact. Durable: `books` is explicitly excluded from the per-session
`TRUNCATE`, and `_ensure_test_database()`'s ingest only re-runs when the table
is empty, so this will not be silently reverted by normal test runs.

**With both databases actually corrected, one golden baseline moved — by
score only, not by ranking.** `recommend_thoughtful_mood` rank 8's score
drifted 0.4904 → 0.4907 (0.0003, just past the 0.0001 tolerance). All 9 ranks'
*titles* were checked and matched exactly, at every position — confirmed by
diffing the regenerated file against its predecessor line by line, not by
trusting the regeneration. This is the expected shape of change: correcting
6,307 books' `language` shifts the `StandardScaler` statistics that feed
`lang_enc` for the whole catalogue by a hair, which can nudge a downstream
score without moving anything's rank. Regenerated through the project's own
`GOLDEN_REGENERATE=1` mechanism, reviewed before committing, not to make a
failure go away.

Full suite after: see the commit this entry ships with.

### F-49 · `CSV_FALLBACKS` points at four files that have never existed — **FIXED, deleted 2026-09-15**

Found during the structure cleanup, logged rather than fixed then: removing a
fallback branch changes what the app does when the catalogue is missing,
which is a behaviour change, not a cleanup — that call belonged to the
product owner, not a structural pass.

`main.py:193` (later `services/catalogue.py`) defined a fallback chain for
catalogue loading:

```python
CSV_FALLBACKS = [
    Path(__file__).parent / "merged_complete_dataset.csv",
    Path(__file__).parent / "google_books_dataset.csv",
    Path(__file__).parent / "dataset_gutenberg.csv",
    Path(__file__).parent / "bookg.csv",
]
```

`git log --all --name-status -- '*.csv'` returns **nothing**: not one of
these four files has existed in any commit in this repository's history,
and none is on disk. `load_books_raw` reaches the branch only when
`site_ready_books.json` is absent, at which point it finds no CSV either
and returns `[]` — so the effective behaviour was already the synthetic-data
path (F-03), just reached one dead branch later.

**Decided (2026-09-15): delete, not restore.** There is no CSV ingestion
route to bring back. `load_books_raw` now falls straight through to `[]`
when `DATA_FILE` is missing, with no read attempt in between.
`DATA_SOURCE` can no longer be `"csv_fallback"` — `lifespan.startup` and
`/health` (`api/health.py`) both collapsed from a three-way
`"json" / "csv_fallback" / "none"` check down to `"json" / "none"`, since the
middle value could never actually occur. The unrelated CLI smoke test
(`scripts/recommend_cli.py`) hardcodes the same four filenames independently
and was left alone — it is not the app's ingestion path, and touching it
would be a second, unasked-for change.

Guarded by two tests in `tests/test_data_loading.py`:
`test_csv_fallback_chain_is_gone_not_reintroduced` (the symbol must not
exist) and `test_missing_catalogue_file_returns_empty_not_a_csv_read` (the
functional behaviour). Sabotage-checked: reintroducing a CSV read behind a
fabricated single-file fallback fails the first test.

### F-50 · The questionnaire page calls an endpoint that is not registered — **FIXED, built 2026-09-15**

`frontend/js/questionnair.js:72`:

```js
const response = await fetch(`${API_BASE}/api/questionnaire/options`);
```

`/api/questionnaire/options` was not among the 45 registered OpenAPI
operations, and matched no route decorator in `main.py` — only
`POST /api/questionnaire` existed. Every call returned 404, silently: the
client has its own try/catch that falls back to a hardcoded question set,
which is why nobody noticed live.

Same family as F-16 (drifted frontend/backend contracts), found the same
way: reading the client against the route list rather than the client
against itself.

**Decided (2026-09-15): build the real endpoint, do not patch the client.**
`GET /api/questionnaire/options` (`api/questionnaire.py`) now exists,
returning `{"questions": [...]}` in the exact shape the client already
expects (`id`, `text`, `options`) — the client itself needed no change.

**The values are not just UI copy.** `mood`, `pace`, `language` and
`popularity` are matched against exact vocabularies downstream
(`QuestionerEngine.MOOD_NORMALISE`, `.PACE_PAGE_MAP`, the literal
`"popular"`/`"underrated"` checks in `_filter_df`, and the language column's
two-letter codes) — an option that isn't in those vocabularies would not
error, it would silently fall through to `"any"`, the same silent-drift
shape F-16 already cost this project once. `mood`'s options are therefore
built directly from `MOOD_GENRE_MAP`'s own keys, not a second hand-written
list that could drift from it. `genre` is different — `_filter_df` matches
it with a case-insensitive substring search, so any real string works — and
is built from the ten most common non-`"Unknown"` genres actually present in
`main.BOOKS`, rather than a guessed list.

**A related, unfixed observation surfaced while verifying this against real
data, not part of this fix.** Cross-checking the new genre list against
`GET /api/books/filter-options` (F-16) found that endpoint's `uniq()` helper
sorts genres alphabetically and truncates at 200 — and the catalogue holds
thousands of distinct raw genre/shelf strings (Goodreads-style tags like
`"35mm cameras"`, `"aboriginal australians"`), so common genres like
`"Fiction"` are **not guaranteed to survive that truncation**. Not a defect
in this fix — the new endpoint reads `main.BOOKS` directly, not through
`filter-options` — but worth a look separately if `filter-options`'s genre
list is ever relied on as complete.

Guarded by three tests in `tests/test_app_boots.py`: reachability, that the
returned mood/pace values are real members of the engine's own vocabulary
constants, and that the returned genres actually exist in `main.BOOKS`.
Sabotage-checked: injecting a mood value absent from `MOOD_GENRE_MAP` fails
the vocabulary test.

### F-47 · `POST /api/recommend` cannot filter by language at all — **FIXED**

Surfaced by the F-26 diff review, not caused by it. `thoughtful_mood` rank 3
became **아웃라이어** — Gladwell's *Outliers*, Korean edition. Semantically a
good match for "thoughtful"; it simply should not reach an English reader.

The two recommendation paths disagree, structurally:

```
QuestionerEngine._filter_df    filters language (answers["language"])
UserProfile                    has no language field at all
RecommendRequest               has no language field either
```

`recommend_by_profile`'s mask covers genre, author, mood, viewed books and
disliked keywords. Language is absent, and no caller can supply it — so the
direct recommend endpoint has no way to express the constraint even in
principle.

**Pre-existing, and my change made it visible.** Better content matching
ranked a genuinely apt Korean book highly, where the old positional ordering
had buried it. The bug was always there; the improvement exposed it. That is
the ordinary way a latent defect surfaces, and worth recording as such rather
than as a regression.

**Why it needs you rather than a patch:** OI-2 was decided as "filter
non-English at query time rather than deleting the rows", so the direction is
settled. What is not settled is the **default**. Defaulting `/api/recommend`
to English changes results for every existing caller and silently hides
catalogue from anyone who wanted it; defaulting to "any" leaves today's
behaviour, which shows Korean and Italian books to English readers. The third
option is to make the parameter required, which breaks callers loudly rather
than quietly.

Not choosing that unilaterally. Logged with the evidence.

**Decided (2026-09-14): default to English, "any" opts out.** Implementation:

* `UserProfile.language: str = "en"` (`domain/entities.py`), `RecommendRequest.
  language: str = "en"` (`schemas/recommend.py`), wired through in
  `api/recommend.py`.
* `services.recommendation.language_mask(column, requested)` — a free
  function, not inlined, because `recommend_by_profile`'s actual candidate
  pool is capped at ~150 by the ANN shortlist and then reshuffled by the
  bandit's exploration slots, which makes testing the filter logic through
  the full pipeline unreliable (see below). Tested directly against a plain
  pandas Series instead.

**The design decision that mattered: unknown language is not excluded.**
32.8% of the catalogue (9,842 books, mostly Goodreads) carries no language
label at all. The questionnaire's own pre-existing filter
(`QuestionerEngine._filter_df`) excludes a book the instant its language is
unrecorded, because `"unknown".startswith("en")` is `False` — fine as an
*opt-in* filter a user deliberately reaches for, but copying that pattern into
a filter that is now **on by default** would have silently dropped a third of
the catalogue from every recommendation nobody asked to be filtered. That is
the exact "unknown is not a negative signal" mistake OI-11 (price) and F-29
(provider lookups) already cost this project real damage to learn — here it
would have applied more broadly than either, since it runs unless the caller
opts out. `language_mask` excludes a row only when its language is
*confirmed* to be something else; a row with no language on record passes
through un-filtered. Verified by sabotage: dropping the pass-through term
fails exactly the two tests written for it and nothing else
(`tests/test_language_filter.py`).

**A second, unintended consequence found while reviewing the diff, before it
shipped.** `UserProfile`'s new default is shared by every caller that
constructs one — including `QuestionerEngine._build_profile`, which never set
`profile.language` at all. That silently pulled the *new* on-by-default
English filter into the questionnaire's ML-scored candidate generation, even
when the questionnaire's own, separately-decided answer said `language: "any"`
— contradicting `_filter_df`'s already-correct handling of that same answer
one line later in the same method. Fixed by having `_build_profile` explicitly
mirror `answers.get("language", "any")` into the profile, so F-47's new
default reaches only the path it was scoped to (`/api/recommend`) and the
questionnaire's pre-existing behaviour is untouched. Caught by running the
golden suite and finding three questionnaire scenarios failing that had no
business being affected by a change scoped to a different endpoint — not by
reasoning about it in advance.

**A test that would have passed either way, caught before it shipped.** The
first version of `tests/test_language_filter.py` asserted end to end, through
`recommend_by_profile(n=2000)`, that an unrecorded-language book's presence in
the result set didn't change with the filter on vs. off. It passed — with the
fix in place *and* with the fix deliberately removed. The pipeline never
returns more than ~150 of the requested 2,000 results (the ANN shortlist cap),
and which of those 150 include any given book is also reshuffled by the
bandit's exploration slots, so an end-to-end sample this small proves nothing
either way. Rewritten to test `language_mask` directly against a plain
`pandas.Series` — deterministic, fast, and this time verified by sabotage to
actually fail when the fix is removed.

**A deeper, pre-existing instability found while reviewing the golden diff,
out of scope to fix here.** Regenerating `questionnaire_popular_english`
after this change returned a *different* random book at rank 10 on repeated
regeneration — never the same one twice. Investigation (documented in the
test file, `tests/test_golden_ranking.py::test_questionnaire_golden`) found
the cause is not this filter: `recommend_from_answers` calls
`recommend_by_profile(n=n*2)` internally, whose bandit exploration slots live
at the tail of that internal list, then intersects the result with
`_filter_df`'s separately-filtered set and truncates to `n`. If the language
filter removes enough of the internal deterministic items, an
exploration-random item can be pulled forward into a position the test's
prefix formula does not predict — the formula was written for the simple,
unfiltered case and never accounted for this method's extra filter-and-
truncate step. A second, separate instability was also found — this one
**not** call-to-call random but fit-to-fit: two near-tied candidates at
`no_preferences` rank 9 and `popular_english`'s tail returned in different
order across separate fresh model fits while agreeing every time within one
fit, which rules out the bandit (its exploration is unseeded and would vary
within a fit too) and rules out `MiniBatchKMeans` (`random_state=42` is
already set). The likely cause is GPU floating-point non-determinism in the
MiniLM embedding pass — not bit-for-bit reproducible across CUDA runs — since
that is the one input to the ranking pipeline that is neither seeded nor
cached across fits and would explain tiny score differences flipping a
near-tied comparison's winner. Not investigated further or fixed: it touches
the core ML fit and deserves its own review, not a side effect of a language
filter that merely shifted which candidates land in a near-tied zone and
exposed a gap that was already there. Both golden tests now use an
evidence-based safe prefix (empirical agreement across two calls for the
call-to-call case; a manually verified cap, `MAX_SAFE_PREFIX`, for the
fit-to-fit case) rather than trusting the formula or a single regeneration —
see the code comments at both sites for the full reasoning and the exact
titles involved.

*Process note, worth recording precisely because it cost real time: my own
ad-hoc verification scripts for this investigation did not set
`POSTGRES_DB=digikitab_test`, so they ran against the production `digikitab`
database — which has materially different accumulated interaction/reading-
depth data from this session's own earlier testing — rather than the clean
`digikitab_test` database `tests/conftest.py` forces for the actual suite.
This produced a substantially different, more alarming-looking ranking result
for `thoughtful_mood` than what the real tests ever saw, and very nearly led
to over-investigating a problem that didn't exist in the database that
matters. `tests/conftest.py` forcing `POSTGRES_DB` regardless of the
environment was already logged once this session (F-48's writeup) for
exactly this reason; this is the second time it has cost real verification
time, this time on the diagnosing side rather than the "which database did I
just fix" side.*

### F-46 · `recommend_by_profile` had never used content similarity at all — **FIXED**

Found by refusing to accept a green golden run. After F-45 was fixed and the
MiniLM path was demonstrably live (`content_space: minilm`, pinned by a test),
all 18 golden tests still passed. A feature-space change that moves nothing is
not a result, it is a symptom.

`recommend_by_profile`, engine.py:1257-1263:

```python
if profile.taste_vector is not None:
    q_idx, q_sims = self.ann.query_vector(profile.taste_vector, ...)
else:
    q_idx  = candidates.head(150).index.tolist()
    q_sims = np.linspace(1, 0, len(q_idx))     # <- "content similarity"
```

**Correction to my own first reading of this.** I initially logged it as
affecting cold-start users, on the assumption that `record_feedback` populates
`taste_vector`. It does not. **`taste_vector` is never assigned anywhere in
the codebase** — it is declared on `UserProfile`, read in three places, and
written in none. `record_feedback` updates the bandit and the exploration
rate only.

So the `if profile.taste_vector is not None:` branch is **unreachable dead
code**, and the `else` runs for *every* profile request, for every user,
regardless of history. `content_s` is always `linspace(1, 0)` over the first
150 candidates in DataFrame order — which is source-file order. A positional
prior wearing the name of a content signal.

```
before feedback:    None
after 2 feedbacks:  None      <- record_feedback does not touch it
ANN branch reachable: False
```

There is a small mercy in this: because the branch is dead, my switching the
ANN to MiniLM did **not** create a mixed-space bug, which it would have if
`taste_vector` were populated in the old TF-IDF space and then used to query a
MiniLM index.

**Measured, not inferred** — ANN calls counted by wrapping the methods:

```
recommend_by_profile (fresh profile)   query_vector 0   query 0
recommend_by_book                      query_vector 0   query 1
```

**Consequences:**

- `WEIGHTS["content"] = 0.28` of the final score is unrelated to content, for
  every user, on every profile-based request.
- `POST /api/feedback` answers `"Feedback recorded — taste model updated."`
  and reports `taste_vector_dim`, which is always 0. The message describes
  something that does not happen.
- It also feeds `content_s` into the LTR feature matrix, so **F-26's finding
  understated the problem**: `content_s` is not merely constant at *training*
  time, it is synthetic at *inference* time too, on the most common path.
- The MiniLM switch (F-44) therefore improves `recommend_by_book` and does
  nothing for `recommend_by_profile` until this is fixed. That is exactly why
  the golden baselines did not move, and the honest reading of "no diff" is
  "the change could not reach this path", not "the change is safe".

**The fix is now cheap and was not before.** With per-book MiniLM vectors in
place, a cold-start profile can be turned into a real query vector: encode the
stated preferences (`"Fiction. dark."`) and ask the ANN. That is a genuine
content signal for a user who has told us what they want but not yet rated
anything — which is the single most common state a recommender sees.

Not doing it in this commit: it changes ranking for every profile-based
request, and this commit's job is the feature-space swap with the baselines
verified. Logged as the next step.


**Fixed 2026-08-31.** `_profile_query` encodes the reader's stated preference
in the book vectors' own idiom — `"Fantasy. adventurous"` against
`"Dune. Frank Herbert. Science Fiction"` — and queries the ANN. A profile that
has rated nothing has still told us something, and that is the most common
state a recommender ever sees.

Three guards, because this replaces a path that always worked:

* no encoder, or nothing stated → the old ordering, unchanged
* every neighbour filtered out by genre/mood → falls through; an *empty*
  content signal is worse than a weak one
* encoder model ≠ book-vector model → refuses and logs. That is the exact
  door the mixed-space bug (F-39) would come through, and relying on
  `taste_vector` being dead to keep it shut was luck, not design.

**Diff review before re-baselining, per the product owner's instruction.**

The clearest evidence the old path was positional: *The Watcher, and other
weird stories* was rank 1 for **three unrelated scenarios** — different
genres, different moods. Three distinct queries could not distinguish
themselves, because ranking followed source-file order. After the fix they
diverge.

```
scenario               was                          now
no_preferences         Tan Lines                    Tan Lines          (identical)
author_only            Skyward                      The Rithmatist     (both Sanderson)
fantasy_adventurous    The Watcher (weird stories)  At the Earth's Core
fiction_dark           The Watcher (weird stories)  The Secret Adversary
thoughtful_mood        Russia and the Russians      Maps of Meaning
```

`thoughtful_mood` is the standout: a history shelf (*Russia and the Russians*,
*The Cambridge Ancient History*, *The Real History of WWII*) became a
psychology and philosophy shelf (*Maps of Meaning*, *Exploring Psychology*,
*Talking to Strangers*).

`no_preferences` is **byte-identical**, which is the most reassuring line in
the output: with nothing stated the encoder is skipped and the old path runs,
so the fallback is provably intact.

**A correction I made mid-review:** from rank 1 alone I judged `fiction_dark`
"arguably worse". The full top-5 shows both are mystery/thriller shelves of
comparable quality — *Four Weird Tales* and *The Grey Room* are as apt as what
they replaced. It is a wash, not a regression. Reviewing rank 1 only would
have recorded a regression that is not there, which is the argument for
reading the whole prefix.

**Verdict: two improvements (one large), two neutral, one unchanged, no
regressions.** Baselines regenerated on that basis, not to make a failure go
away.

### F-45 · A golden run reported success for a change it never executed — **FIXED**

Switched content similarity to MiniLM, ran the golden baselines, got
`17 passed`. For a feature-space change, "nothing moved" is implausible — and
it hadn't:

```
test db:  29,975 books,  2,097 vectors   ->  loader refuses, falls back
```

The test database held only the 300-row sample a fixture had built.
`load_content_vectors` did exactly what it is designed to do — refuse a
partial set rather than zero-fill — so the engine ran on TF-IDF and the
baselines were re-verified against **the space I was replacing**.

This is F-43 in a different costume, and the third time this session the same
shape has appeared: **silence and success looking identical**. F-30's watcher
could not see a crash; F-43's suite passed with no database; this passed
without running the code under test.

Fixed twice over. The test database now has full vector coverage, and
`test_the_baselines_describe_the_space_they_were_recorded_in` pins
`content_space` *before* any baseline is compared, so a fallback can never
again be mistaken for a pass — in this change or a future one.
`EXPECT_CONTENT_SPACE` overrides it for deliberately testing the fallback.

*Generalisable rule, now stated so it stops being rediscovered: **a test that
can pass without executing the code under test is not a test of that code.**
When a change should move something and the suite is green, suspect the
harness before believing the result.*

*A third rule: **a test that greps source text will eventually read a comment
or docstring as code — and the file most likely to describe a bug in prose is
the file that contains its fix.** Two real occurrences, both 2026-09-21/22,
both the same shape — the prose explaining what was fixed matched the pattern
searching for whether it was broken:*

*1. OI-5's `test_the_entrypoint_does_not_bind_every_interface`,
`..._does_not_force_the_reloader` and `test_cors_admits_nobody_unless_asked`
all failed on correct code, because `main.py`'s comment quotes the old
`host="0.0.0.0", reload=True` it replaced. 2. F-61's
`test_clearing_user_state_does_not_delete_the_catalogue` matched the
`TRUNCATE ... CASCADE` inside the docstring explaining the bug — and that one
was worse than a red test, because it failed on **every** run and so appeared
in three unrelated sabotage results as their "catcher", making three
sabotages look caught when nothing had caught them.*

*Counted honestly, it has happened twice, not three times; two further cases
(F-54's field sweep, OI-5's raw-exception sweep) were written with comment
stripping from the start and so never failed. The rule is being recorded at
two rather than waiting for a third, because the second instance corrupted a
sabotage table — a failure mode that reports false confidence rather than a
false alarm.*

*The remedy is cheap and should be the default: **parse, do not grep.**
`ast.parse` the module and inspect the node — `ast.get_docstring` to drop the
docstring, `ast.unparse` to get statements back as text — so the test can only
ever see code. Where a text search genuinely is the right tool (a sweep across
many files for a forbidden pattern), strip comment lines first and say in the
test why the weaker check is acceptable there.*

*The corollary, earning its own line because it has now happened three times
(F-22 Phase C twice, Phase D once): **a sabotage that fails nothing means the
test is too weak, not that the code is fine.** Each time, a guard that
genuinely worked was being "proved" by an assertion that would have held
either way — a reader-named-book exemption, a smuggled `user_id`, and a Redis
`ltrim` whose absence was hidden because the test read through a capped
`load()` rather than checking what was stored. Worth watching for: when a
deliberate break comes back green, tighten the test before trusting the
guard.*

**Known limitation, deliberately accepted:** the test database has
`with_description = 0`, so its vectors are all metadata-only, while production
has 6,998 richer ones. The baselines therefore pin *deterministic behaviour*,
not production ranking quality. That is the right trade for a regression
guard, but it means a golden diff is evidence about stability, not about
whether recommendations got better. Quality is measured separately, in the
series-recall and qualitative comparisons above.

### Measuring the content-similarity switch — **two of my three metrics were useless**

Before wiring `content_s` onto the new vectors, I measured. The first attempt
mostly measured nothing, which is worth recording because the failure mode is
easy to repeat.

```
                     tf-idf    minilm    delta
same-author@10        32.1%     32.5%    +0.3     <- uninformative
same-genre@10        100.0%    100.0%    +0.0     <- broken
series-recall@10      94.7%     98.1%    +3.4     <- real
```

**`same-genre@10` is broken.** 39.9% of the catalogue has `genre = "Unknown"`,
and the sample (top by `ratings_count`) is dominated by exactly those rows. It
was matching noise to noise and reporting a perfect score for both methods. A
metric that reads 100% for two visibly different systems is measuring the
sample, not the systems.

**`same-author@10` is uninformative.** Both feature sets contain the author
string, so both retrieve same-author books equally well. It measures metadata
overlap, not semantics.

**`series-recall@10` has real ground truth** — series membership parsed from
titles like `(Chicagoland Vampires, #3)`, restricted to series with more than
one member present so a miss is a genuine miss. MiniLM wins by 3.4 points.

**The aggregate understates it, and the qualitative gap is large:**

```
seed: Dracula
  tf-idf   Metamorphoses; The Omen; Us
  minilm   Twice Bitten (Chicagoland Vampires); The Vampire Armand; Micah

seed: Pride and Prejudice and Zombies
  tf-idf   Northanger Abbey; A Thousand Acres; Tao Te Ching
  minilm   Eligible: A Modern Retelling of Pride and Prejudice; Persuasion
```

Both readings are true, and the reconciliation is the useful part: the
metrics are dominated by easy cases — series volumes with near-identical
titles — where TF-IDF already succeeds. The improvement is concentrated in
the hard cases where the words differ and the meaning does not. *Dracula* to
vampire novels rather than *Metamorphoses* is the whole point, and no
aggregate over this sample surfaces it.

**Conclusion: proceed, but do not expect the ranking metrics to jump.** The
honest claim is better neighbours on hard cases, not a large aggregate win.

### F-42 · The data moat was never being filled — **FIXED**

`interaction_events` and `recommendation_log` shipped in **PR 2** with indexes
and docstrings that say, in the tables' own words:

> *"It ships before any model work because interaction history cannot be
> backfilled — a week not logged is a week lost permanently."*
> *"Phase 3 is gated on this table having accumulated data — so it starts
> filling now, in Phase 1."*

Nothing ever wrote to either one. Both were empty. `grep` finds no reference
outside `models.py` and the migration.

```
InteractionEvent   0        Comment           0
RecommendationLog  0        ReadingProgress   0
```

Six call sites now log: `search`, `book_view`, `recommend`, `feedback`,
`comment`, `progress`. `recommendation_log` records the **shown** set with
ranks — a later click means nothing without the books that were offered and
passed over, and those exist nowhere else.

Three properties, all cheaper to build in than retrofit:

1. **Logging never breaks the request.** A recommendation that 500s because
   analytics failed is strictly worse than one nobody measured.
2. **A failed log never poisons the caller's transaction** — events write on
   their own session. This is the F-30 lesson one layer over: an INSERT that
   raises inside a shared session makes the *next* statement fail too, so the
   user's actual request dies of an analytics error.
3. **Anonymous is signal.** `user_id` is nullable deliberately; dropping
   anonymous traffic would discard most of what a young product sees.

Event types are a closed vocabulary — a typo'd `event_type` is a silent hole
in the data six months later, so `record()` refuses unknown ones.

### F-43 · The suite reports success when the database is down — **FIXED**

Found by accident: `tests/test_events.py` returned `10 skipped in 131s`,
**exit code 0**, and nothing said why. Docker Desktop had stopped again
(fourth time). `database_reachable()` swallows its exception and returns
False, so every database-dependent test skips and the run passes.

A green run that tested nothing is worse than a red one. Same shape as the
watcher that could not see a crash: silence reading as success.

Now the terminal summary names it — how many tests were lost and why — and
`REQUIRE_DATABASE=1` fails at configure time, before the expensive ML fixture
runs. Local runs stay skippable, because needing Postgres up to run the
pure-logic tests would make the fast path slow.

### F-41 · A CPU embed took the machine to 100 °C — **FIXED, now on GPU**

Reported by the user mid-run, not by any check of mine. Three things were
competing for every core: the MiniLM re-embed on CPU-only torch, and two
stray `pytest` runs left over from backgrounded commands, each fitting the
full ML stack.

**Immediate:** killed all three. CPU load fell to 6%.

**Root cause, and why it was invisible.** `sentence-transformers` falls back
to CPU whenever CUDA is missing or the wheel is the CPU build, and says
nothing. The only symptom is that it takes far longer — which is
indistinguishable from "the corpus got bigger". I installed `+cpu` torch
deliberately (to avoid contending with a training job for the GPU) and never
revisited it when that job's exposure turned out to be nil.

**Fixed on the hardware that was there all along:**

```
torch 2.13.0+cpu  ->  2.11.0+cu128     RTX 5070 Ti, sm_120 (Blackwell)
0.09 ms/chunk vs 11.0 on CPU           124x
137,317 chunks                         ~12s, was ~25 min
```

Verified as *execution*, not availability: model parameters on `cuda:0`,
90.9 MB allocated after load, peak 82% GPU utilisation and 2.3 GB GPU memory
sampled from `nvidia-smi` during the run. `torch.cuda.is_available()` alone
would not have proved anything.

**The silent fallback is now impossible.** `EMBEDDING_DEVICE=cuda` is a
promise that is checked — the backend raises rather than starting on CPU. On
an explicit `cpu` it caps threads at half the cores and says so at WARNING,
because an unattended batch job taking every core is how this happened.
`describe()` reports device, torch build and live GPU memory, so /health and
any future check can see what is actually running.

*Generalisable lesson: a performance decision made to avoid a constraint
should be revisited when the constraint disappears. The GPU was idle at 44 °C
for the entire CPU run.*

### F-38 · `/api/books` silently ignored unknown query parameters — **FIXED**

Approved 2026-08-30. `?max_price=5` returned all 29,975 books with 200 —
FastAPI discards parameters a route never declared. Every typo behaved the
same way: `limt`, `genr`, `ratingmin`.

Middleware, not per-route, so a route added later cannot forget it. It
resolves the route itself because Starlette has not matched one at middleware
time, and it walks sub-dependencies so a shared pagination dependency's
parameters still count as declared.

The 422 names both the unknown parameters and the accepted ones — a rejection
that does not say what would have worked just moves the guessing. Cache
busters (`_`) are tolerated: they are added by HTTP clients, not written by
the caller, and failing a request over something the caller never typed is
not honesty.

Eight tests, including that declared parameters still work and that a
path-only route is not mistaken for a query-less one.

### F-40 · Tables of contents were being indexed as though they were the book — **FIXED**

Found in a live search result, not by reading code:

```
"a terrifying story set in a haunted house"
  top hit -> The Wonder Book of Bible Stories
  passage -> "THE STORY OF NOAH AND THE ARK 7  THE STORY OF HAGAR AND
              ISHMAEL 16  THE STORY OF ABRAHAM AND ISAAC 22 ..."
```

`extract_reading_text` keeps front matter deliberately — it is part of the
opening pages a reader paginates through (F-17). Embedding it is a different
question: a contents block yields a confident vector for prose the model never
saw, which is worse than having no vector at all.

`is_front_matter` now filters the text path at chunking time. Descriptions
were already covered upstream by `looks_like_prose`.

**The first version of the filter did not catch its own motivating example.**
The Bible Stories contents had no "Chapter" keyword and no leading enumerator
— it was titles with *trailing page numbers*. That is now a third signal, and
it is the test named `the-hit-that-found-this-bug`.

**Measured on 8,000 real chunks: 4.6% flagged.** Spot-checking eight found six
clear contents blocks, one dedication, and one genuine false positive. So
roughly 1% of real prose is lost to remove roughly 3.5% of junk — worth it,
and bounded: if the filter would empty a book entirely it is overruled, since
indexing something beats indexing nothing.

**Applies to new chunks only.** The existing 81,636 carry the old content
until a re-chunk, which is the same pass that a MiniLM cutover would run
anyway.

### F-39 · A backend switch would have silently mixed two vector spaces — **FIXED**

Found while planning the MiniLM cutover, before it could do damage.

`embed_pass` selected only rows where `embedding IS NULL`. After a backend
change the existing 75k rows keep their LSA vectors while new chunks get
MiniLM ones — and cosine similarity between two different vector spaces is
meaningless. Search would have degraded badly while **every individual row
still looked correctly embedded**. `embedding_model` recorded the mix; nothing
acted on it.

Now a row whose vector came from another backend is re-embedded like a missing
one, so switching backends is self-healing and a mixed space is unreachable
without `--redo`. Two behavioural tests, sabotage-checked; the second pins
that an ordinary run does *not* become a full re-embed.


`torch` is already present, so the install is small, but it resolves
dependencies inside a shared global Python (F-24) while an unrelated training
job is running (F-33) and could move `numpy` or `torch` underneath it. Search
currently runs on TF-IDF+SVD, which works and cost nothing. Switching later is
`EMBEDDING_BACKEND=minilm` plus `embed_pass --redo` — the seam is already
there. **Recommendation:** wait until that training run finishes, then install
into a virtualenv rather than the global interpreter.

### OI-8 · Windows sleep is the root cause of the lost runs — **HANDLED BY THE PRODUCT OWNER, 2026-09-15**
The containers now restart with the daemon and a supervisor relaunches dead
passes, but neither prevents the host from sleeping mid-run. Long unattended
passes will keep being interrupted until the power plan is changed
(`powercfg /change standby-timeout-ac 0`) or the runs move to a machine that
stays awake. Flagged rather than changed: altering a developer's power
settings is not a repository decision — the product owner applied it
directly on the host.

### OI-7 · Google Books quota is the binding constraint — **DECIDED AND SHIPPED, 2026-09-16**

Measured: ~1,000 queries/day against 12,671 remaining English records (of
22,818 pending overall), one to two queries each. That is two to four weeks
of wall-clock at the current allowance. See F-31.

**Decision: run the standing job at the current quota now, rather than wait
idle for the increase request to land.** If the increase never lands, this
still makes steady, real progress instead of zero. If it lands, the job
accelerates automatically — deliberately not by adding logic that detects
the new ceiling, but by not needing to: `scripts.enrich` already stops a run
the instant Google's real daily limit is hit (`QuotaExceeded`,
`services/providers/base.py`, existing since Phase 2). The standing job's
`--limit` is set to 25,000 — far above both the current ~1,000/day quota and
the entire remaining pending count — so `QuotaExceeded` is what actually
paces the job every day, at whatever the real ceiling happens to be that
day. A larger quota just means it fires later in the same run. No code
changes are needed when the increase lands; there is nothing to switch on.

**What was built:**

* `scripts/run_daily_enrichment.ps1` — the wrapper Task Scheduler invokes.
  Its job is narrower than pacing: this machine's Docker Desktop does not
  survive a reboot or a sleep (F-30, OI-8), and `scripts.enrich` has no
  reachability check of its own — pointed at a database that isn't there
  yet, it raises a raw `psycopg.errors.ConnectionTimeout` traceback rather
  than a clean message (confirmed live while building this). The wrapper
  launches Docker Desktop if it isn't running, starts the two `digikitab-*`
  containers specifically (this machine also runs an unrelated project,
  finmentor, on the same Docker daemon — never touched), polls
  `pg_isready`, and only then runs `scripts.enrich`. If the datastores never
  come up within two minutes it logs why and exits non-zero rather than
  hanging or crashing raw.
* A Windows Scheduled Task, `DigiKitabDailyEnrichment`
  (`scripts/DigiKitabDailyEnrichment.xml`), daily at 04:00, `StartWhenAvailable`
  so a missed run (machine off or asleep at 4am) catches up the next time the
  machine is on rather than silently skipping a day. Registered
  `InteractiveToken` / least-privilege, not `S4U` or a stored password —
  Docker Desktop is a GUI app tied to the logged-in session, so the task can
  only run "when the user is logged on," which is the honest constraint of
  this machine, not a workaround.
* `logs/enrichment/run_<timestamp>.log` per run, plus a running
  `logs/enrichment/history.log` — one line per run with exit code and the
  `processed` / `ok` / `partial` / `quota_exhausted` / `description_coverage`
  counts, so whether the job is actually accelerating when the quota changes
  is visible at a glance without opening Task Scheduler's history.

**A real, non-cosmetic bug found and fixed while building the wrapper, not
part of the standing-job feature itself.** The first version piped the
Python subprocess's output through PowerShell with `2>&1`. PowerShell 5.1
wraps a native process's stderr lines — which is where Python's `logging`
module writes by default, so every `[INFO]` line — in `NativeCommandError`
objects when captured that way, which showed up as spurious error noise in
the log even on a real exit code of 0. Fixed by using `Start-Process` with
explicit `-RedirectStandardOutput`/`-RedirectStandardError` files, which
redirect at the OS level and never route through PowerShell's own error
stream. Verified by comparing the same run's log before and after: identical
`run:`/`catalogue:` summary content, zero `NativeCommandError` noise after.

**Verified live, twice, through two different paths, before being
considered done** — this project's standing discipline (F-48's dry run,
F-45's "a test that can pass without executing the code under test is not a
test," etc.) applied to infrastructure rather than code this time:

1. A direct 15-book run against the real key and real database: 7 `ok`
   (full description), 8 `partial`, 0 failed, 0 throttled — confirming the
   new `GOOGLE_BOOKS_API_KEY` (received 2026-09-16, credentials table below)
   actually works. It briefly returned `503 backendFailed` on the first few
   probe requests, which is not a quota or auth failure — this project's own
   F-31 documented the identical error shape on the previously-issued key
   for the same reason: Google Books' search backend is intermittently
   flaky under any key. A direct volume lookup and a plainer query both
   succeeded immediately; the exact `intitle:`/`inauthor:` structured query
   `enrich_one` actually uses succeeded on a bare retry. `fetch_json`'s
   existing retry ladder (`RETRY_STATUS` includes 503) already absorbs this
   in production; nothing needed changing.
2. The wrapper script invoked directly (containers already up, then
   containers deliberately stopped first to confirm the auto-restart path),
   and separately triggered through `schtasks /Run` — the actual mechanism
   the 04:00 trigger will use, not a proxy for it — with the containers
   stopped beforehand. Both: Docker/Postgres came back automatically,
   `scripts.enrich` ran, exit code 0, correct summary in both log files.

**Hardened 2026-09-16 for a laptop that isn't always on at 4am.**
`StartWhenAvailable` (Task Scheduler's "run as soon as possible after a
missed start") was already set from the first version — verified live on
the registered task, not assumed from the XML — so a missed 04:00 already
caught up at the next opportunity Task Scheduler itself checks for one
(service start, resume from sleep). Added a second trigger, `LogonTrigger`
scoped to this user, so catch-up also fires deterministically the moment the
laptop is actually turned on and logged into, rather than only on whatever
cadence Task Scheduler's own missed-trigger sweep uses.
`MultipleInstancesPolicy=IgnoreNew` (already set) means the two triggers
landing close together — e.g. logging in right around 4am — can never cause
a double-run; `scripts.enrich` is idempotent by design either way (only
`pending` rows are selected).

Two power-setting facts checked, not assumed, since a wrong assumption here
would silently defeat the whole point: `powercfg` shows this machine already
never sleeps on AC (`STANDBYIDLE` = 0, the OI-8 fix), so the job survives
being plugged in and idle; on battery it still sleeps after 3 minutes, which
is fine — catch-up doesn't need the machine to stay awake, only to notice
once it's next on. `WakeToRun` is deliberately left off: it would make Task
Scheduler try to wake the laptop from sleep *at* 4am on its own, which is a
different, larger ask (needs AC power and BIOS/OS wake-timer support most
laptops disable on battery) than the one made — "catch up when I turn it on
or it wakes up [myself]" — so it was not built. Flagged as an available
option, not assumed to be wanted.

**Not done, deliberately:** no attempt to detect or react to the quota
increase landing — see above, there is nothing to detect. No auto-stop of
the containers after the run; they are lightweight and the product owner's
own dev workflow likely wants Postgres available during the day anyway, so
the job leaves them running rather than tearing down infrastructure that
was already a deliberate `restart: unless-stopped` decision (PR 2).

### OI-6 · Frontend needs a login UI — **SHIPPED, 2026-09-17**

Comments, progress and reminders all require a token now, so the existing
pages got 401. This is the fix.

**Re-prioritised 2026-09-11, alongside F-26's answer.** Reading depth is now
the relevance target, and it was **book-level only**: with no login and no
visitor id, a page turn could not be attributed to a reader. Login is what
turns it into personalisation. Concretely, it makes reachable:

| Unlocked by login | Before |
|---|---|
| per-reader depth — *this* reader's furthest page | only aggregate, via the survival curve |
| Reading DNA (§25), derived from a reader's own history | no history could be attributed |
| explicit ratings — `comments.rating`, `POST /api/feedback` | both existed; neither reachable |
| progress and completion — `reading_progress` | existed; nothing wrote it |
| impression -> click attribution | no identity to join on |

**Scope, decided before writing anything.** Two questions were asked and
answered before implementation, because both change the shape of the work:

1. *How does the login UI reach the backend?* **Directly, at
   `http://127.0.0.1:8000`** — the same convention `questionnair.js` already
   used — rather than through `server.js`'s proxy. Investigation found the
   proxy does not forward the `Authorization` header at all, so a login flow
   routed through it would silently 401 on every protected call regardless
   of a valid token. Fixing the proxy, or retiring Express (which PROGRESS.md
   had loosely bundled with OI-6 in the note above), were both explicitly
   declined as separate, larger decisions — not something a login-UI task
   should absorb by default.
2. *How is a `token_expired` response handled, given no refresh endpoint
   exists?* **Re-prompt for login.** `POST /api/auth/refresh` was not built.
   Tokens last 7 days by default (`JWT_EXPIRE_MINUTES`), so re-authenticating
   on the rare expiry is an acceptable trade against adding a new endpoint
   nobody asked for yet.

**What shipped — frontend only, zero backend changes.** `register`/`login`/
`GET /api/auth/me` already existed, fully working, since PR 2 (F-07) and
OI-10 — this was a frontend gap, not a backend one.

* `frontend/js/auth.js` — the one shared module every page includes.
  Token storage, `authFetch()` (attaches `Authorization` automatically and
  clears the session on any `401` — `token_expired`, `token_invalid`,
  `account_inactive` all mean the same thing to a caller: log in again),
  `login()`/`register()`/`logout()`, and `mountAuthNav()`, which renders into
  a `<span id="auth-slot">` placeholder added to each page's nav. There is no
  shared header/nav include in this frontend (six standalone HTML files,
  each with its own copy of the topbar markup) — introducing one was out of
  scope for a login feature, so the placeholder-element approach was used
  instead: one small, identical edit per page rather than a restructure.
* `frontend/login.html` — a combined login/register page, reusing
  `styles.css`'s existing `.card`/`.btn`/`.input` classes rather than
  inventing new ones. Redirects to a `?next=` path after success, rejecting
  anything that isn't a same-site relative path (`/…` or `./…`) to close an
  open-redirect vector before it existed.
* `index.html`, `filter.html`, `Audiobook.html`, `questionnair.html`,
  `user.html` — each gained the `auth-slot` nav element and an `auth.js`
  `<script>` include. `chatbot.html` was left untouched: it has no nav
  markup of any kind (a pre-existing gap, not something to fix inside this
  task).
* `user.html`'s comment and reminder handlers were rewritten to use
  `AUTH.authFetch` and to check `AUTH.isLoggedIn()` before firing a request,
  showing a plain "log in to do this" prompt instead of a silent failure.

**A real bug found and fixed while wiring this up, not a hypothetical.**
`user.html`'s comment handler sent `{user_id, book_id, comment, rating}` —
but `CommentRequest` dropped `user_id` and added `extra="forbid"` back when
F-07 closed this hole (the author is always the token holder now). That
field was never removed from the frontend when the backend changed, so the
call was broken two independent ways at once: no `Authorization` header
(401) *and* a body shape the schema now rejects outright (422, confirmed
live: `"Extra inputs are not permitted"`). Comments could not have been
posted from this page since F-07 shipped, silently — `if (response.ok)`
gated the success path and did nothing visible otherwise. Verified fixed
live, end to end, against the real backend and real Postgres: register →
`/api/auth/me` → post a comment (200) → set a reminder (200) → list
reminders (200) → the same comment call with no token (401) → the old
broken body shape (422, confirming the bug was real) — then the two test
users and their rows deleted from `digikitab` before finishing, since this
was exercised against the real dev database, not a disposable one.

**CORS checked, not assumed.** `auth.js` calling `:8000` from a page served
at `:3000` is a cross-origin request, and attaching `Authorization` forces a
preflight. Verified live: `OPTIONS /api/comments` from `Origin:
http://127.0.0.1:3000` returns the right `Access-Control-Allow-*` headers,
and an authenticated `GET /api/auth/me` from that origin succeeds — `main.py`
already allowed `:3000` by default, so this needed confirming, not building.

**Not done, deliberately:** no refresh endpoint (see the scope decision
above); `server.js`'s proxy still does not forward `Authorization` (not
touched, by the same decision — every new call bypasses it entirely);
`chatbot.html` still has no navigation. `GET /api/profile/{user_id}` exists
and is now reachable with a real login, but no page's UI reads it yet —
logging in unlocks the backend capability this table describes; building a
profile page around it is a follow-on, not part of "add a login UI."

### OI-5 · Deployment readiness — **CLOSED 2026-09-21.** Both the recreate and F-55 landed; see "Applied and verified" below
**Status (2026-08-19):** product owner confirmed the app is **local-only until further notice**, so the rate limit was deliberately **excluded from PR 1**.

**This is a deployment gate.** `/api/audiobook/generate` is unauthenticated (F-07) and synchronous (F-19). After PR 1 it can no longer destroy files, but each call still triggers an unbounded fetch-and-synthesise — repeated calls exhaust the server.

**Before the app becomes reachable from any network, ALL of:**
- ~~authentication (F-07)~~ — **done in PR 2**
- ~~read-authz sweep~~ — **done in PR 3**
- ~~per-IP rate limit on `/api/audiobook/generate`~~ — **done**, 3/min/IP
- ~~move generation to a background job (F-19)~~ — **done 2026-08-30**, 202 +
  poll with a bounded registry. (This line read "still open" until
  2026-09-20: a stale status, three weeks after the work landed.)
- `JWT_SECRET` set in the environment (config refuses to boot without it
  when `ENV=production`; the `.dev-jwt-secret` fallback is development-only)
- token revocation (still no denylist) — accepted risk when written, and now
  **revisitable**: Redis is no longer hypothetical, it runs the rate limiter
  and conversation history

**Re-scoped 2026-09-20. Three gates this list predates, all created since it
was written:**

- ~~**`POST /api/chat` has no rate limit**~~ — **done 2026-09-21.** It took
  `OptionalUser`, so anonymous callers were supported by design, and each
  request could drive up to `MAX_STEPS = 4` local-model calls on the single
  GPU: a strictly worse denial-of-service lever than the endpoint OI-5 was
  originally written about. 10/min per caller, plus a concurrency guard.
- ~~**`CORS_ORIGINS` defaults to `localhost:3000/8000`**~~ — **done
  2026-09-21**, by removing the second origin rather than by editing the
  allowlist. The default is now empty.
- ~~**Single worker, still**~~ — **resolved 2026-09-21 as a documented
  constraint with a guard that enforces it**, not a rewrite.

---

## OI-5, scoped then implemented — 2026-09-21

**The scope changed the answer.** OI-5 had been carried for a month as "the
audiobook endpoint needs a rate limit". Auditing before touching anything
found the rate limit was the *smallest* of its gates. Everything in this
table was verified against the running system, not inferred from code:

| What | How it was verified | Status |
|---|---|---|
| Redis published on `0.0.0.0:6380` with **no password at all** | `docker ps` showed `0.0.0.0:6380->6379`; `redis-cli DBSIZE` answered with no credential | **fixed and verified** |
| Postgres published on `0.0.0.0:5433`, password defaulting to `digikitab_dev` in both compose and `config.py` | same `docker ps` | **fixed in compose — see "still to apply"** |
| `main.py` entrypoint: `uvicorn.run(host="0.0.0.0", ..., reload=True)` | source | **fixed** |
| `ENV` defaults to `development`, so every production guard in `config.py` was inert | source | **fixed** — guards now enumerate what is wrong |
| `/docs`, `/redoc`, `/openapi.json` public | live request | **fixed** — gated on `ENV` |
| Raw exception text returned to callers in 5 handlers, one on public `/health` | source sweep | **fixed** |
| Express (`server.js`) dead code whose only real effect was forcing a second origin | every frontend page hardcoded `:8000`; its `/api` proxy stripped the prefix, dropped `Authorization`, and `.json()`-ed binary responses | **retired** |
| Uvicorn started without `--proxy-headers`, while the limiter reads `request.client.host` | source | **F-55 — decided: leave as is, blocked on OI-3** |
| Secrets hygiene | only `.env.example` ever committed; no key in history; no git remote configured | clean |

### What was built

**1. `POST /api/chat` is rate limited** — 10/minute, charged before the
engine-ready check and before any model work. `/chatbot` is the same handler
under a second path and shares the budget; a limit keyed on the path would
have been bypassed by alternating between the two.

The bucket is the **IP always, and the account as well when signed in**. Not
the account *instead of* the IP: that reads like the tighter choice and is
the looser one, because registering an account costs an attacker one request
and would have bought them a fresh allowance. The account bucket exists only
to stop one account roaming addresses; with equal limits it never binds
otherwise.

**2. A concurrency guard, which is the part that actually protects the GPU.**
A per-caller rate limit bounds how fast *one* address can ask. It does not
bound how many ask at once, and 100 callers each staying politely inside
10/min still buries one GPU. `services/providers/llm.py` now holds a 2-slot
semaphore, taken across the whole tool loop rather than per `chat()` call —
a per-call slot can be granted for step 1 and refused for step 2, which
abandons a half-finished answer *after* spending the GPU time that produced
it. The unit of work is the request.

Over capacity it raises `LLMUnavailable`, so **saturation and an outage take
the same path out**: the classifier answers. Section 12 asks for degradation,
and a cheap deterministic reply now beats a good reply after the queue
drains.

**3. Express retired.** `server.js`, `package.json` and `package-lock.json`
deleted; FastAPI serves `frontend/` from its own origin via `StaticFiles`,
mounted last so every API route is already claimed. This is the real CORS
fix: two origins for one app was the *reason* `allow_origins` had to name
localhost, and an allowlist you must remember to edit before deploying is a
gate rather than a setting. `CORS_ORIGINS` survives as a deliberate escape
hatch, defaulting to empty. The seven `API_BASE` constants in the frontend
are now `''`.

`scripts/run.ps1` replaces `npm start`, and exists for one reason: it picks
the venv interpreter. Not cosmetic — sentence-transformers is not in the
shared global Python (F-24), and starting from the wrong one does not crash,
it returns nothing for every semantic query (OI-9).

**That failure mode demonstrated itself during this work.** The full suite
was run with the global interpreter and produced 8 failures and 5 errors
that read as genuine regressions from this change set — golden ranking,
book vectors, the librarian's catalogue search. Re-run under the venv:
clean. It does not announce itself; it looks like a bug in whatever you just
touched. Recorded here because the lesson generalises past the launcher.

**4. Single worker, documented *and* enforced.** `core/jobs.check_single_worker()`
runs first in `startup()`, ahead of the catalogue load and ML fit, and
refuses a multi-worker launch (`--workers N`, `-w N`, `WEB_CONCURRENCY`).

Kept as a constraint rather than rewritten onto Redis because nothing here
wants a second worker: the ML fit runs per process and takes tens of
seconds, the fitted model is hundreds of megabytes resident, and the
librarian is bounded by one GPU that a second worker would only contend for.
Two workers would cost more and serve less.

A constraint is only documented if something enforces it. Unenforced, the
symptom is an audiobook job that 404s on poll roughly (N-1)/N of the time —
which reads as a bug in synthesis, not as a deployment flag.

Stated so it is not mistaken for complete: the guard cannot catch a process
manager starting N copies of the app each with `--workers 1`. Nothing inside
one process can see its siblings.

**5. Declaring production is now enough to be told what is wrong.**
`config.production_problems()` enumerates default credentials and `DEBUG`;
`ENV=production` refuses to boot while any remain, and `/health` reports the
list as `production_blockers` — gated on `DEBUG`, because `/health` is public
and "POSTGRES_PASSWORD is the development default" is a useful sentence to
the wrong reader. Nothing is lost by hiding it: in production the list is
empty by construction. `ENV` still defaults to `development`, which is right
for a laptop; the point of the guards is that *declaring* production is a
complete check rather than the start of one.

**This one was claimed before it was true.** The `production_blockers` key
was added to `config.summary()`, which `/health` does not call — so the list
was unreachable and the sentence above was false when first written. Its
test asserted on `config.summary()` rather than on the endpoint, so it passed
throughout: the same shape as "a correct limiter the route never calls",
which this file already warns about twice. Caught by curling the running app,
not by reading either file. The test now probes `/health`.

### Sabotage results

Seven sabotages, every one caught:

| Sabotage | Caught by |
|---|---|
| Route never calls the limiter | 5 tests |
| Limit runs after the engine-ready check | `test_the_limit_applies_before_the_engine_ready_check` |
| Signing in replaces the IP bucket instead of adding to it | 2 tests |
| Limit keyed on the request path | 6 tests |
| `slot()` leaks capacity when the work raises | 2 tests |
| `slot()` is per-thread instead of shared | 6 tests |
| The engine never takes a slot | `test_the_engine_takes_a_slot_before_running_the_tool_loop` |

The first attempt at sabotage 1 produced invalid Python and reported "30
passed, 10 errors" — which is not a sabotage that passed, it is a sabotage
that never ran. Redone with an `ast.parse()` check on the mutated source
before the suite runs. **Worth noting beside the recurring weak-sabotage
rule (F-45): this is that rule's mirror image, and at a glance it reads the
same.** A sabotage that fails nothing means the test is weak; a sabotage that
*errors* means you have learned nothing at all.

### Applied and verified — 2026-09-21

Both containers were recreated under this repository's compose file, after
waiting for the OI-7 enrichment job to reach its **natural** pause point
rather than restarting Postgres underneath it. The job is resumable by
design (`enrichment_status` committed every 25 books), so a restart would
have been survivable — but it would have spent up to 24 books of that day's
Google quota on work it could not record, and the quota is the binding
constraint (F-30). Waiting cost nothing: it stopped on `QuotaExceeded` after
992 books, exit code 0.

Verified after, not assumed:

```
digikitab-postgres  127.0.0.1:5433->5432   ->  final_postgres_data
digikitab-redis     127.0.0.1:6380->6379   ->  final_redis_data
books                 29975
by_status             pending 21274 / ok 5930 / partial 2018 / not_found 751 / failed 2
redis-cli DBSIZE      NOAUTH Authentication required.
app's own REDIS_URL   connects
```

The `by_status` line matches the enrichment run's own closing report exactly,
which is what confirms the 992 books survived the recreate.

**This step nearly went wrong — see F-56.** The first `docker compose up -d`
mounted an empty volume.

```
docker compose up -d        # when enrichment is idle
```

**A separate finding that deserves its own line:** the running containers
belonged to compose project `final`, working directory `D:\final\final\final`
— a *stale copy of this repository*. `final_clean/docker-compose.yml` had
never governed them. Editing it and stopping there would have been a fix
that did nothing while reading as done. Same family as the `digikitab` /
`digikitab_test` confusion: **the config you are editing is not always the
config that is running.**

**OI-5's list is closed.** What remains before this app faces a network is
no longer a list of defects but two open decisions: OI-3 (deployment target),
which F-55 now blocks on, and token revocation. The app stays local-only
until OI-3 is answered.

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

### F-22 · The chatbot never returns recommendations — **FIXED 2026-09-19**
*(all five phases landed; the librarian is complete as scoped)*
`ChatbotEngine.respond()` initialises `response["books"] = []` and never
populates it, for any intent. It classifies intent correctly
(`"recommend me a dark thriller"` → `recommend`, confidently) then returns a
canned string and an `action` label. **The "AI chatbot" is an intent
classifier with hardcoded replies and no connection to the recommender.**

Covered by `test_chatbot_returns_recommendations`, marked `xfail(strict=True)`
so it will fail loudly the moment it starts working and the marker can be
removed.

**The original note here said wiring it to `recommend_by_profile` "is small."
That is still true of the narrow fix and no longer the goal.** The product
owner's intent (2026-09-18) is the AI Librarian of prmpt.md §28 — a model that
actually converses and composes tool calls against the catalogue, not a
classifier that finally returns a `books` list. Scoped before building (a
new subsystem, not a patch: provider abstraction, agentic tool loop,
fallback design, conversation state, and a search-coverage gap surfaced
along the way), then sequenced into five independently shippable phases,
each confirmed with the product owner before the next starts:

| Phase | What | State |
|---|---|---|
| A | Wire `book_vectors` into book-level search (100% catalogue coverage, was ~23%) | **Done**, `a861994` |
| B | `LLMProvider` interface + local-model fallback chain | **Done** — below |
| C | The tool loop: `search_catalog`, `check_availability`, `check_user_library`, `get_reading_profile` wired into `ChatbotEngine.respond()` | **Done** — below |
| D | Conversation history (server-side, Redis) | **Done** — below |
| ~~E~~ | ~~Resolve the `xfail(strict=True)`; real coverage for the tool loop and fallback path~~ | **Folded into C** — the xfail is `strict`, so the suite fails the moment the fix works. Leaving it for a later phase was never an option once C landed. |

**Decided: local model, not API-based.** Ollama was already installed on this
machine (0.33.2) and already held `qwen2.5:7b` and `qwen2.5:3b` — no API key,
no per-call cost, stays consistent with the app's local-only posture. The
real risk of a local model is reliability at the multi-step tool composition
§28 asks for, so it was measured before being chosen, not assumed:

| Model | VRAM (`ollama ps`: 100% GPU) | §28's own worked example |
|---|---|---|
| `qwen2.5:7b` | 4.7 GB | correct tool (`search_catalog`), query extracted, exclusion (`James Clear`) extracted |
| `qwen2.5:3b` | 2.2 GB | same, ~0.3s |

GPU: RTX 5070 Ti Laptop, 12.8 GB total, ~10 GB free at the time (the rest is
ordinary desktop compositing — browsers, shell, Docker Desktop — not a
competing ML workload; `torch 2.11.0+cu128` correctly recognises it as
`sm_120`/Blackwell). Both models fit alongside MiniLM's ~90 MB with room to
spare.

**Phase B — built.** `backend/services/providers/llm.py`, next to the
`BookProvider` adapters and following the same §10 principle ("every external
dependency must sit behind an interface"): `LLMProvider` (a `Protocol`),
`OllamaProvider(model)`, `FallbackLLM([...])`, `get_llm_provider()` returning
`qwen2.5:7b` then `qwen2.5:3b` (overridable via `LLM_MODEL` /
`LLM_FALLBACK_MODEL` / `OLLAMA_BASE_URL`), and one specific exception,
`LLMUnavailable`, raised only once every tier has failed.

Two design decisions worth recording rather than leaving implicit:

* **The classifier is not a tier in this chain.** The scoping request framed
  the fallback as "qwen2.5:3b or the existing classifier." The classifier has
  no chat shape — no free-form generation, no messages/tools abstraction —
  so wrapping it as a fake `LLMProvider` would be the wrong abstraction. The
  full §12 chain is 7b → 3b (this module) → `LLMUnavailable` → `ChatbotEngine`'s
  existing classify-and-template behaviour (Phase C wires that last step;
  this module's job ends at raising one catchable exception).
* **`FallbackLLM` catches `LLMUnavailable` only, not `Exception`.** A bug in
  a provider (a `KeyError` in response parsing, say) falling silently through
  to the next model would hide a real defect behind a working fallback.
  Sabotage-checked: widening the `except` fails exactly
  `test_a_non_llm_error_is_not_swallowed_as_unavailability`.

Not built on `providers.base.fetch_json`: that helper is GET-only and tuned
for the metadata backfill's retry ladder and `QuotaExceeded`; a chat call is
one POST with a JSON body against an endpoint with no daily quota. Sharing it
would have meant bending it, not reusing it.

Thirteen tests (`tests/test_llm_provider.py`), in two deliberately separate
groups: the chain's own logic against fake providers (deterministic, no
Ollama needed), and the real wiring against the actual local models,
skipped-not-failed when Ollama is unreachable — the same convention as
`database_reachable()`, since a green run on a machine that cannot run the
model would be exactly the "test that can pass without executing the code
under test" F-45 named. The live group uses *real* failures rather than
mocked ones: a request for a model that was never pulled (Ollama answers
`404 {"error": "model '…' not found"}`, verified against this instance before
the test was written) and a port with nothing listening. Three sabotages,
each caught by exactly the intended tests and no others: breaking the
fallthrough (3 failures, including the live fallback test), widening the
`except` (1), and letting raw `URLError`/`HTTPError` escape instead of
converting them (4).

**Three days blocked, 2026-09-17 → 2026-09-20 — see F-53.** Google returned
`403 (blocked, not a miss)` and the circuit breaker did exactly what F-29
built it to do: stop after three consecutive throttles, leave the rows
`pending`, save progress. The job exited 0 each time and nothing said the
catalogue had not moved. The block lifted on its own.

**Measured, not assumed:** ~0.3s warm for either model on a short answer
(the ~2.4s figure from the scoping run was true cold start with the model
unloaded), and a dead primary costs ~nothing to fall past — the failed
attempt is a fast 404. One thing noticed and left alone: `qwen2.5:7b`'s free
text on one probe began with a stray `">` — a cosmetic model quirk on prose,
irrelevant to tool calls, but a reason Phase C should look at real generated
answers rather than assume they render cleanly.

**Phase C — built.** `backend/services/librarian.py`: the tool loop, the four
§28 tools, and the grounding guards. `ChatbotEngine.respond()` calls it first
and falls back to its own classifier on `LLMUnavailable`; the response now
carries `mode` (`"llm"` / `"classifier"`) so which path answered is visible
rather than guessed at.

Three structural decisions:

* **No tool takes a user id.** §28's sketch has `check_user_library(user_id)`.
  Here identity comes from the authenticated request and nowhere else — a
  model that can pass a `user_id` can be talked into passing someone else's,
  and "what has user 7 been reading" is exactly the read F-07's authz sweep
  closed. `/api/chat` now takes `OptionalUser`, never `payload.user_id` (a
  free-text profile key anyone can send). Sabotage-checked both tools that
  read a library.
* **Book data reaches the reader from tool results, never from the prose.**
  The `books` list is assembled from catalogue rows the tools returned, then
  narrowed to the ones the answer actually mentions, so caption and cards
  agree. The model's text is a caption on that list, not a source for it.
* **A bug in the loop is not an outage.** Only `LLMUnavailable` triggers the
  classifier fallback. Swallowing everything would be F-30's
  `except Exception: continue` again — one error quietly becoming all of them.

**Every guard in this file exists because a real run produced the failure it
catches.** None were designed in advance:

| Real output | Guard |
|---|---|
| `"a price tag of $35.00 and is currently available"` — for a book whose price is unknown, after one search and no availability check | `_facts_problem`: a currency amount must match one `check_availability` returned; price/availability *wording* requires the tool to have been called at all; any other figure (years, ratings) must appear in tool output or the reader's own message |
| `"rated 4.0035747133"`, `"similarity score of 0.753"` | raw scores are no longer shown to the model — rating is rounded, similarity is not sent |
| `"I couldn't find any dark thriller books"` — zero results, because the model passed `genre: "thriller"` and 39.9% of the catalogue is genre-`Unknown` | the `genre` parameter is gone from the schema; an argument that sneaks in anyway does not filter |
| flagged `"Atomic Habits"` as invented — the reader had named it in their own message | quoted spans the reader used are exempt |
| `"A narrative exploring the enigmatic life of Jay Gatsby…"` — a description invented for a book with none | placeholder descriptions are never offered as summaries, and the model is told to say the catalogue holds none |

An answer that fails any guard is replaced by a deterministic sentence built
from the tool rows, and `grounded: false` is reported. The title guard's limit
is written down in the code rather than papered over: it only catches titles
written the way the prompt asks for them (in quotes), which is *why* the
`books` list never depends on the prose.

**The `">` quirk, measured rather than left as an anecdote:** 1 of 32 bare
one-sentence answers from `qwen2.5:7b`, 0 of 32 from the 3b, and none in any
tool-loop answer read while building this. Cosmetic — the title survives intact
and the guards normalise punctuation — but it would render on screen, so
`_clean()` strips exactly that pattern (a quote fused to a `>` before a word
character) and nothing else. **Not more than cosmetic.**

28 tests in `tests/test_librarian.py`, all deterministic (fake model, fake
catalogue) plus four against the real app. Six sabotages; two initially passed
— the reader-named exemption and the smuggled-`user_id` guard were both being
proved by tests too weak to fail, so the tests were tightened until each
sabotage failed exactly its own test. F-22's `xfail(strict=True)` in
`test_app_boots.py` is now a real passing test: `strict` means the suite fails
the moment the fix works, so Phase E's "resolve the xfail" could not wait.

**Phase D — built.** `backend/core/conversations.py`: the transcript lives
server-side in Redis (already in the stack for the rate limiter), not in the
request. A browser carrying the whole transcript grows every request as the
chat goes on, and lets a client rewrite what it claims to have been told.

**The key is the load-bearing decision here, not the storage.**

    signed in   ->  the account id, and nothing else
    anonymous   ->  an opaque 128-bit token this module mints, the client echoes

`ChatbotRequest.user_id` is *not* usable as a key: it is free text any caller
can set (`"guest"` by default), so keying a private transcript on it would
rebuild F-07's hole — anyone reads anyone's chat by guessing a name. A
signed-in caller's key comes from their token and a supplied `conversation_id`
is ignored, so it cannot hand them someone else's thread either; `_valid_token`
rejects anything that is not one of ours, without which
`conversation_id="u:5"` would read account 5's history. Bounded on every axis
that a client can push: 12 messages, 2000 characters each, a one-hour TTL, and
a capped in-process fallback for when Redis is down (degrade and warn, never
500 — the rate limiter's precedent).

**A regression this introduced, caught by a live multi-turn probe rather than
by reasoning.** With history in place, the first three follow-ups in a real
conversation all came back *"I couldn't find anything in the catalogue
matching that."* — `"who wrote the first one?"` was rejected because
`ctx.seen` only knew the tools called *this* turn, and the book had been
grounded a turn earlier. Fixed by carrying the grounded book ids forward with
the assistant turn (not shown to the model; they exist so the next turn knows
those rows came from a tool). Only an assistant turn may carry them — a client
declaring `book_ids` on its own message must not be able to assert a book into
being grounded.

The same probe confirmed the guards still bite with history in play: two later
turns were rejected for inventing *"13 Things I Learned about Death and
Dying"* and *"Pride and Prejudice"*, both true positives, both replaced with
tool-grounded answers.

`chatbot.html` now carries the token in `sessionStorage` and sends it back;
its `state.history = []`, declared and never used since the page was written,
is gone — the server keeps the transcript now.

39 tests across `tests/test_conversations.py` and the history section of
`tests/test_librarian.py`, run against both the in-process store and a real
Redis. Eight sabotages. **One initially passed:** removing the Redis `ltrim`
failed nothing, because the test asserted on `load()` — which reads only the
last N regardless, so the stored list could grow forever and the test would
still be green. The same weak-test shape as Phase C's two; the test now
asserts on `llen`, what Redis actually holds.
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

### F-27 · Three catalogue sources, not two — 1,576 books were being deleted — **FIXED**

**F-25 was diagnosed wrong, and the real bug was mine.**

Of the 1,576 duplicate-id groups, **zero** are identical records. All 1,576
are entirely different books sharing an id:

| id | Record A | Record B |
|---|---|---|
| 2149 | A Song of Ice and Fire (#1–4) | The Works of Edgar Allan Poe, Vol. 3 |
| 7947 | ESV Study Bible | The Diary of a U-boat Commander |
| 9806 | One Piece, Volume 38 | Mr. Justice Raffles |

The catalogue merges **three** sources, two of which use plain integer ids
whose spaces overlap:

```
google_books  13,219   alphanumeric volume ids
goodreads     10,449   integer ids
gutenberg      6,307   integer ids
```

`infer_source` (added by me in PR 2) keyed on id shape alone and called every
integer id `goodreads`. Goodreads #2149 and Gutenberg #2149 collapsed onto one
key, and the ingest's own de-duplication **silently discarded one book from
each of the 1,576 colliding pairs on every run.**

**Fix:** the thumbnail URL is the reliable discriminator (`gutenberg.org`,
`books.google.com`, or the local placeholder cover); id shape is now only the
fallback. Plus `ingest --prune`, because an upsert only converges rows it
touches and the correction stranded 4,731 rows under keys nothing maps to.

**Result:** 29,975 books, 0 dropped. Guarded by
`test_goodreads_and_gutenberg_ids_do_not_collide`.

### F-25 · "1,576 duplicate books" — **RESOLVED, was not a data problem**
Superseded by F-27. No ISBN evidence was needed; the titles settle it. The
source file is fine. Nothing should be de-duplicated, and the earlier
"provisional pending ISBN enrichment" decision is moot.

### F-28 · A migration that would have failed in production — **FIXED**
`ALTER TABLE books ADD COLUMN enrichment_status VARCHAR(16) NOT NULL` raised
`NotNullViolation` against the 28,399-row test database. It passed on the dev
database **only because that database happened to be empty at that moment** —
so the failure would have surfaced first in production, the one database
guaranteed not to be empty.

Fixed with `server_default` in both the migration and the model, verified by
upgrading a populated table. `test_not_null_columns_carry_a_server_default`
now scans every migration for the same mistake.

*Generalisable lesson: a migration verified only against an empty database is
not verified.*

### F-32 · A 67% failure rate that was self-inflicted, not a provider block — **FIXED**

The relaunched Gutenberg pass reported `unavailable` for 233 of 350 books. The
obvious reading was that Gutenberg had started blocking us after tens of
thousands of requests, and the circuit breakers agreed — Open Library stopped
itself with "provider is refusing traffic".

Both readings were wrong. Breaking the errors down by class:

```
229  Errno 11001  getaddrinfo failed      <- DNS, not HTTP
  2  WinError 10060  timed out
  1  WinError 10053  connection aborted
  1  timed out
```

Not one HTTP status among them. Nothing was refusing us; **we could not
resolve the hostnames.** Resolving the same three hosts twelve times each from
one idle process succeeded 36/36 in 1.3 seconds, which ruled out the network
being down.

The cause is that `urllib` opens a fresh connection per request, so every
fetch paid a fresh DNS lookup. Three passes running for hours meant tens of
thousands of lookups, and the Windows resolver began refusing under the churn.
The earlier `WinError 10013` ("socket access forbidden") entries are the same
exhaustion in different clothing.

Fixed with a process-wide TTL DNS cache (`backend/net_cache.py`). Effect,
measured on the first 25 books after the change:

```
before   116 ok / 350 attempted     33%
after     25 ok /  25 attempted    100%
```

Six tests, including two that guard the ways a DNS cache can be worse than the
bug: distinct hosts must never be conflated, and failures must never be cached
(a cached failure turns one blip into a permanent outage).

*Generalisable lesson: "the provider is blocking us" and "we cannot resolve
the provider" produce identical-looking failure counts. Breaking errors down
by class before acting on them took two minutes and reversed the diagnosis.*

### Phase 2 — semantic search shipped, with a measured quality number

`GET /api/search/semantic` is live. Coverage as of this run:

```
chunks       29,257   (25,898 book text + 3,359 descriptions)
embedded     29,257   100%, backend=lsa
books                 2,462 searchable, from 1,081 an hour earlier
query latency    66 ms over 25,898 vectors, full scan
```

Descriptions were added because full text exists for Gutenberg alone, so
indexing only text meant semantic search covered ~3% of the catalogue.
Descriptions are what every other enriched book has.

The chunks carry an `origin` column (`text` | `description`). Section 11
requires the licensing basis of every stored passage to be explicit, and these
differ: one is public-domain prose, the other a third-party blurb. It also
matters for retrieval — a chapter and a blurb are different kinds of evidence
that a book matches — so `origin` is returned with every search result.

**Retrieval quality, measured rather than eyeballed** (`python -m scripts.eval_search`):

```
origin=text          73.3%   same-book-in-top-10, 150 probes
origin=description   56.5%
```

Descriptions score lower and should: a blurb and a chapter of the same book
share far less vocabulary than two chapters do.

The harness earned itself immediately. Dropping the first SVD component is
standard advice for LSA — every vector shares that direction, which inflates
similarity uniformly, and 100% of the corpus has the same sign there. Measured:
**68.0% → 67.3%**. No improvement. Without the number it would have shipped on
the strength of the reasoning.

**Honest limitation.** LSA produces confidently wrong answers on queries whose
concepts are not lexical. "A book about grief and losing someone" returns
*Thinking In C++* at 0.89 similarity. The distribution is healthy (corpus mean
0.044, p99 0.53, and a nonsense query scores 0.000 across the board), so this
is not noise being ranked — it is genuinely what LSA thinks is closest. The
fix is a transformer backend, which is OI-9 and needs a decision, not more
tuning. The seam is already in place: `EMBEDDING_BACKEND=minilm` plus
`embed_pass --redo`.

**Deferred with a measurement, and the projection was wrong.** At 29k vectors
a full scan cost 66 ms, and I projected ~380 ms at 150k by scaling linearly.
Re-measured at 75,104 vectors: **60 ms** — no worse, slightly better. The scan
is not the bottleneck at this size; Postgres parallelism and cache absorb it.

So the ANN index stays deferred (§8), but on evidence rather than on my
arithmetic. Re-measure again past ~250k chunks instead of trusting either
number.

### OI-5 · Rate limit — **CLOSED.** Background job (F-19) — **CLOSED 2026-08-30**

Both halves are now done. What remains of OI-5 as a deployment gate:

| gate | state |
|---|---|
| per-IP rate limit on generate | closed |
| F-19 background job | **closed** |
| `JWT_SECRET` set in the environment | **still required** |
| **single worker process** | **new constraint, see below** |

### F-19 · Audiobook synthesis ran in the request thread — **FIXED**

`POST /api/audiobook/generate` now returns **202** with a job id and a poll
URL; `GET /api/audiobook/jobs/{id}` reports `queued` / `running` / `done` /
`failed`.

**The bug demonstrated itself.** Writing the OI-5 tests, three calls hung the
whole suite past 120 seconds — an unauthenticated denial of service
reproduced by accident. Sabotage-checking the fix reproduced it again on
demand: reverting to inline synthesis hung the suite at exactly the same
point, after failing five of the nine new tests.

**Backgrounding alone would not have fixed it.** An unbounded queue moves the
exhaustion rather than removing it — a client can enqueue thousands of jobs
and occupy the workers just as effectively. The registry is bounded at both
ends: two workers, eight pending, and 503 with `Retry-After` past that.
`test_an_unbounded_queue_would_only_move_the_problem` holds that line.

Also fixed: gTTS has no timeout of its own, so a wedged job would report
`running` forever. A job past `JOB_TIMEOUT_SECONDS` is reported failed.

**Deliberately in-process, not Redis or Postgres.** TTS cannot resume in a
process that died, so persisting job rows would make jobs look durable
without being durable — a `running` row surviving a restart describes work
that will never finish. An empty registry after a restart is the truthful
answer.

**The cost is a real constraint:** this is correct for **one worker process**.
With several, a job accepted by one is invisible to the others and polling
404s. Recorded above as a deployment gate. Redis is the fix when multi-worker
becomes real — the same degradation the rate limiter already documents.

**Frontend impact, as accepted:** the audiobook flow must now POST, read
`job_id`, and poll. The old single-request flow is gone.



`POST /api/audiobook/generate` now enforces **3 requests per minute per IP**,
before any work is done.

Design notes worth keeping:

* **Fixed window, not a token bucket.** A fixed window lets through up to 2x
  the limit across a boundary. That is the textbook objection and it does not
  matter here: 2x of a very small number is still a very small number, and the
  goal is to stop one client occupying every worker. Two Redis commands,
  correct by inspection (§8).
* **A Redis outage degrades, it does not fail open.** Redis is most likely to
  be down under exactly the load a rate limit exists to survive, so "open"
  means no protection precisely when it is needed. It falls back to in-process
  counters — correct for one instance, which is what this is, and degrading to
  per-worker limits if it is ever scaled out. Written down so that is a known
  property rather than a surprise.
* **`X-Forwarded-For` is ignored.** With no trusted proxy in front it is
  attacker-controlled, so honouring it would let anyone reset their own limit
  with one header. A limiter that a single line of curl bypasses is worse than
  none, because it reads as protection.

Verified against the live Redis container, not only the fallback: refused
after exactly 3, `retry_after=60`, a second client unaffected.

**OI-5 remains BLOCKING.** The rate limit was one of its three conditions.
Still open: F-19 (move generation to a background job) and `JWT_SECRET` set in
the deployment environment.

### F-19 demonstrated itself while these tests were being written

The first version of the endpoint tests hung the suite past 120 seconds on
three calls, because `generate` performs an unbounded network fetch and a TTS
call **in the request thread, with no timeout**. Three requests from one test
were enough to stall it.

That is precisely the denial-of-service shape OI-5 exists for, reproduced by
accident. The tests now stub synthesis — the hang is recorded as evidence, not
designed around — and it is a concrete argument for F-19 rather than a
theoretical one.

### Boot verified against a real server, not the test client

A green suite is not a booted app, and TestClient has diverged from a real
ASGI server in this project before. Booted uvicorn against the dev database:

```
/api/health           ok:true, 29,975 books, ml_ready:true, clusters:9
/api/search/semantic  backend lsa:501a37e8, real passages from real books
/api/books            200/200 -> availability=unknown, price=null
/api/recommend        12 results, all availability=unknown
```

No non-Gutenberg book reports a price of 0 on either path (F-36).

Search results carry no `availability` field — they are passages, not book
records — and the frontend's new logic falls through to "Price unknown"
rather than "Free" when it is absent. The fix fails safe, now confirmed
rather than assumed.

Server stopped and the port confirmed released afterwards. That is asserted
rather than trusted because of the earlier false-pass restart test.

### F-37 · The guard against mixed vector spaces had a hole — **FIXED**

F-35 stops a query encoded by one backend being ranked against vectors from
another, by comparing `embedding_model`. Setting up a job to keep chunking as
enrichment ran surfaced the case it missed:

**refitting LSA on a larger corpus produces a completely different vector
space, and it was still called `"lsa"`.** The mixed vectors would have sailed
through the check built to catch exactly that. Worse, it is the likely path in
practice — the corpus grows every hour, so refitting is routine.

The cause is that LSA is not like a pretrained model. Its space is *defined by
the corpus it was fitted on*, so the corpus has to be part of the identity.
`LsaBackend.name` is now `lsa:<fingerprint>`, a digest of the learned
components: two fits over different corpora differ, refitting the same corpus
does not (so it does not force a pointless full re-embed).

Refit on the grown 29,257-chunk corpus and re-embedded. Everything now reads
`lsa:501a37e8`. Quality after the refit:

```
origin=text          73.3%  (unchanged)
origin=description   52.2%  (was 56.5% — 150 probes, so within noise)
```

*Generalisable lesson: a guard is only as good as the identity it compares.
"Which algorithm" and "which fitted model" are different questions, and only
the second one is safe to rank against.*

### F-36 · The app told users every book was free — **FIXED**

Section 18: *"Do not fake price or availability. If no provider is configured:
availability = unknown, not fabricated."*

Measured against the live database:

```
total 29,975   list_price > 0: 0   list_price = 0.00: 29,975
```

**Every book in the catalogue carries a 0.00 placeholder.** Both serialisers
passed it through as a price, and both frontends render it the same way:

```js
const price = book.price === 0 || book.price == null ? 'Free' : ...
```

So the product displayed **"Free"** on cards for 9,842 Goodreads titles and
13,826 Google Books titles — copyrighted works this platform has no right to
give away and no basis for pricing. Not a hypothetical: it is on the
recommendation cards users see.

Fixed by making the rule explicit and shared:

```
gutenberg            -> 0.0,  "free_public_domain"   (public domain,genuinely free)
list_price > 0       -> price, "listed"              (a real figure, kept)
anything else        -> None,  "unknown"             (section 18's default)
```

`price_and_availability()` is used by both the catalogue and recommendation
paths. It was inline in each before, which is how they could have disagreed
about what a book costs while only one got fixed. Both frontends now say
"Price unknown" rather than "Free".

**The fix broke the ML fit, and the tests caught it.** `_books_to_df` feeds
`b["price"]` back in as the `list_price` feature; `None` becomes NaN and
`GradientBoostingRegressor` refuses to fit, which silently disabled the entire
recommender. The boot tests failed immediately with "ML engine did not fit".
The feature column now takes 0.0 for unknown prices — a number, not a claim —
which also keeps ranking numerically identical.

**Bonus evidence for F-26.** The engine's only use of this column is
`1/(1 + list_price)`, and every row is 0.00, so that feature has been a
constant 1.0 for all 29,975 books. One of the seven dead LTR features now has
a measured root cause. Left for Phase 3, where the ranking work belongs.

**Open question for the product owner (OI-11):** `max_price` filtering treats
an unknown price as 0, so "books under $5" currently returns the whole
catalogue. Honest alternatives are to exclude unknown-price books from a price
filter (accurate, returns nothing today) or to keep including them with a
caveat in the response. That is a product call, not a code one.

### F-35 · Two embedding models in one column rank noise confidently — **FIXED**

Found by a test failing for the wrong reason. An endpoint test seeded chunks
with the deterministic `hashing` backend, the route encoded the query with
`lsa`, and searching for a passage *verbatim* returned nothing. The passage
was right there.

Cosine similarity between two different vector spaces is a number, not a
measurement. It does not error and it does not return zero — it returns
plausible-looking rankings computed from noise. In production the trigger is
ordinary: switch `EMBEDDING_BACKEND`, run `embed_pass` over the new chunks,
and now the column holds two spaces and every search silently mixes them.

`search_chunks` now takes `embedding_model` and the route passes the active
backend's name, so only comparable vectors are ever ranked together. The
column was recording the model already (that was deliberate); nothing was
*using* it, which made it documentation rather than a safeguard.

*Generalisable lesson: a test failing for an unexpected reason is worth more
than a test passing. This one was written to check authorization and found a
correctness bug in ranking.*

### F-34 · An invalid token silently becomes an anonymous request — **CLOSED, was already covered by OI-10**

`get_current_user_optional` used to return `None` for a forged or expired
token in exactly the same way it does for no token at all. So a caller with a
bad token got **200 with anonymous results** rather than 401.

For semantic search this was more than cosmetic: a user whose token expired
silently stopped seeing their own private passages, and nothing told them why.
The search looked like it worked and quietly returned less.

Not fixed at the time, deliberately: `OptionalUser` is shared by every read
endpoint, so changing it meant expired tokens would start returning 401
across the whole API — a product decision about session expiry behaviour,
not a patch to make inside a search PR (§2, one deliberate change at a
time). Logged as an `xfail(strict=True)` in `test_search_endpoint.py` and as
OI-10.

**Checked 2026-09-15, per the product owner's instruction to verify OI-10's
fix already covers this before applying anything new: it does, fully.**
`get_current_user_optional` (`auth.py`) is the *single* shared dependency
behind `OptionalUser`, used by every route that needs it — currently
`api/search.py` (where this was first found and fixed) and
`api/books.py::book_detail`. OI-10's fix lives at that one shared point, not
per-route, so both call sites already raise 401 with `code: "token_invalid"`
or `"token_expired"` rather than downgrading to anonymous. No code change was
needed here; the `xfail` in `test_search_endpoint.py` was already converted
to a real, passing regression test when OI-10 shipped.

Added one more direct test,
`test_an_invalid_token_401s_on_every_optionaluser_route_not_just_search`
(`tests/test_app_boots.py`), hitting `/api/books/1` rather than search — to
demonstrate the fix is genuinely at the shared dependency and not an
accident of the one route it was first noticed on. Sabotage-checked:
reverting `get_current_user_optional` to return `None` on any decode failure
fails exactly that test.

### F-33 · This machine is running a second heavy ML workload — **CONTEXT, NEEDS AWARENESS**

While identifying which processes to restart, `Win32_Process` showed ~25
unrelated Python processes: `SportsStrategyCoachAI` running
`training.train_ball --part 2 --total-parts 4` with a multiprocessing pool,
plus large downloads from `exrcsdrive.kaust.edu.sa`.

Left completely alone — it is not mine to touch. But it explains a great deal
that had been attributed to this project: the socket and DNS exhaustion behind
F-32, and plausibly the Docker Desktop stops behind F-30, which look like
memory pressure. It also explains why the earlier watcher never fired: it
treated "any python.exe is alive" as "the passes are alive", and these
processes kept that condition true while our passes were dead.

**Implication for scheduling:** enrichment throughput here is not
provider-bound, it is host-bound. Running more passes in parallel makes things
worse, not better. Two gentle passes now, rather than three aggressive ones.

### F-31 · The Google pass is quota-bound, not code-bound — **DECIDED VIA OI-7**

Probed the API directly rather than inferring from the failure logs:

```
with key : HTTP 503  "Service temporarily unavailable"  reason=backendFailed
no key   : HTTP 429  "Quota exceeded for quota metric 'Queries' and limit
                      'Queries per day' of service books.googleapis.com"
```

The keyed project is being throttled after a burst, and the anonymous path is
fully exhausted. The default Books API allowance is **~1,000 queries/day**, and
each book costs one to two queries. So the 12,786 remaining English records
need **roughly two to four weeks** at the current allowance, no matter how the
code is written.

This is not something more engineering can fix. Both circuit breakers now stop
cleanly and save progress, which is the correct behaviour — but the ceiling is
the quota, and the quota increase request is the only thing that moves it.

**Decided (2026-09-16, OI-7): run the standing job at the current quota
now** rather than wait idle for the increase, accelerating automatically
if/when it lands. See OI-7 for the full decision record and
implementation — this entry is kept only as the original discovery record.

### Both new guards proved themselves within 9 minutes of shipping

Not a finding — a note, because it is the sort of thing that is easy to claim
and hard to evidence. F-29 and F-30 were fixed at 14:30 and both fired in
production almost immediately:

```
[ERROR] database unreachable after 106 book(s): (psycopg.errors.AdminShutdown)
        terminating connection due to administrator command
[ERROR] Provider is refusing traffic after 96 book(s). Stopping; progress is
        saved. Resume later.
```

The first is F-30: Docker Desktop stopped again and the pass stopped **once**,
cleanly, instead of converting the remaining 5,600 books into failures. The
second is F-29: Google blocked, and the run stopped instead of marking 12,786
books permanently `not_found`. Before that morning both would have been silent
data loss.

### Docker Desktop stopping is now the top operational risk — **MITIGATED**

Three stops in three unattended runs. Two mitigations, and the split matters:

* `restart: unless-stopped` on both containers — brings the datastores back
  **with the daemon**, verified via `docker inspect`.
* A supervisor loop watching log staleness, because the restart policy cannot
  help when the *daemon itself* is gone. It starts Docker Desktop, waits for
  `pg_isready`, and relaunches only passes whose log has been silent past
  several times its normal logging interval — so it can never run two copies
  of the same pass.

Neither stops Windows from sleeping, which is the actual root cause and is a
host setting, not a repository one. Logged as OI-8.

### F-30 · One dropped DB connection voided the rest of a 6,000-book run — **FIXED**

At 00:18, ~35 minutes into the three unattended passes, **Docker Desktop
stopped** and took Postgres with it. The passes did not stop. They kept going
with a session stuck in a failed transaction:

```
[WARNING] book 47758: (psycopg.OperationalError) consuming input failed:
          could not receive data from server ... (0x00002745/10053)
[WARNING] book 47760: Can't reconnect until invalid transaction is rolled
          back. Please rollback() fully before proceeding
```

`except Exception: ... continue` caught the error but never called
`session.rollback()`, so every subsequent book failed identically. One
transient blip silently converted the remaining ~5,700 books into failures.

Fixed two ways: roll back before continuing, and treat `OperationalError` as
fatal for the run — if the database is gone, every remaining book will fail
the same way, so stop and say so rather than logging 5,700 copies of one
error. Committed batches are untouched; a re-run resumes from them.

*Generalisable lesson: `except Exception: continue` around a database session
is not resilience. Without a rollback it converts one error into all of them.*

### F-29 · Unreachable was being recorded as "book does not exist" — **FIXED**

`fetch_json` returned `None` both when a provider answered "no match" and when
we never reached the provider at all (DNS failure, socket error, timeout).
`enrich_one` cannot tell those apart, so it wrote
`enrichment_status = 'not_found'` — and `pending_query` only ever selects
`pending`, so **the row is permanently excluded from every future pass.**

Two triggers, both observed live in these logs:

```
[WARNING] request failed after 4 attempts: <urlopen error [Errno 11001] getaddrinfo failed>
[WARNING] HTTP 403 for .../books/v1/volumes: <!DOCTYPE html> ... unusual traffic
```

The 403 is the dangerous one. It is how Google blocks traffic, it is not in
`RETRY_STATUS`, and it fell straight through to `return None` — so a block
during the quota-funded Goodreads pass would have marked **thousands of books
permanently missing, at full speed, in a run nobody was watching.** Worse, the
quota to look them up again is the scarce resource: a false `not_found` means
paying for the same book twice.

Fixed with `ProviderUnreachable`, raised instead of returning `None`. It
subclasses `ProviderThrottled`, so `enrich.py`'s existing circuit breaker
already does the right thing with zero changes there: leave the row `pending`,
and stop the run if it keeps happening.

Guarded by four tests, **validated by sabotage** — reverting the fix fails
exactly the two new tests and nothing else. `test_404_is_still_a_real_miss`
holds the other side of the line, so `not_found` keeps its meaning.

Blast radius while the bug was live: 133 `not_found` rows, against only 5
network warnings in that log. So at most ~5 are false. Left alone; re-running
them costs no quota if it ever matters.

### F-26 · 7 of 10 LTR features have zero importance — **SKEW FIXED, TARGET DECIDED: READING DEPTH**

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

---

**Fixed 2026-08-31 — the skew half. Read the expectations note below before
judging this by the ranking.**

The diagnosis sharpened while fixing it. Five of the ten features are
**query-dependent**: `content_s`, `cf_s`, `cluster_match` and `mood_match`
only mean anything relative to a seed, and `comment_score` is zero for every
book at fit time. Training pointwise over the catalogue left no seed to be
relative to, so the code passed `ones` for two and `zeros` for two more. This
was never an oversight — it was **train/serve skew**, and there was nothing
sensible to put in those columns given the training shape.

The fix gives training the same shape as serving: sample 400 seeds, take each
seed's 60 nearest candidates, and build the row relative to that seed — using
`self.ltr._features`, **the same function inference calls**. Sharing the
function is what stops the skew returning; two parallel implementations would
drift the first time either changed.

**Measured. Column standard deviations in the 24,000-row training matrix:**

```
content_s        0.09081     was ones
cf_s             0.10620     was ones
cluster_match    0.49553     was zeros
mood_match       0.15701     was zeros
comment_score    0.00365     varies now, barely (only 4 comments exist)
inv_price        0.00000     still constant - see below
```

Only `inv_price` remains constant, and **for a data reason rather than a
construction one**: every `list_price` is 0 (F-36, no availability provider),
so `1/(1+0)` is 1.0 for every book. It will vary on its own if real prices
ever arrive. The test excludes it explicitly rather than silently.

Zero-importance features went from **7 of 10 to 3 of 10**.

**Expectations — this removes a defect, it does not improve rankings today.**

```
log_ratings        0.5136
log_ratings_norm   0.4722     <- 98.6% of importance, still popularity
avg_rating         0.0143
cf_s               0.000012
recency            0.000002
content_s          ~0
cluster_match      ~0
```

The four repaired features are now *visible* to the model and carry almost no
weight. That is the correct and predicted outcome: **the target is still
circular.** `relevance = 0.4*rating + 0.6*norm(log(ratings_count))` is a
function of three of the model's own inputs, so popularity is the only thing
there is to learn. Features that do not predict popularity will be weighted
near zero no matter how well they are constructed.

So the honest claim is narrow: the model can now *see* content, collaborative,
cluster and mood signal, where before it was structurally blind to them. When
the target is replaced with something real, that fix will land on a model that
can act on it. Without this, it would have landed on one that could not.


**Diff review before re-baselining.** `6 failed, 20 passed` overstated the
change considerably. Separating *reordering* from *score drift* — the golden
fingerprint stores `final_score` to 4 dp, so a retrained model fails a test
without moving a single book:

```
ORDER CHANGED (2 of 9)
  author_only        Sanderson titles reshuffling under an author filter
  thoughtful_mood    아웃라이어 enters at rank 3   -> became F-47

ORDER IDENTICAL, scores only (7 of 9)
  fantasy_adventurous, fiction_dark, no_preferences,
  questionnaire_{empty_answers, fantasy_dark_fast,
                 popular_english, selfhelp_motivational}
  max |Δscore| 0.0000 - 0.0095
```

Seven of nine reordered nothing; two of those failed on a `Δ` of exactly
0.0000. Had I re-baselined on the summary I would have recorded "the F-26 fix
moved six scenarios" — true in letter, misleading in substance — and missed
the Korean-language finding entirely, since it is invisible unless you read
*which* book moved and ask why.

*Worth raising rather than quietly relaxing: including `final_score` at 4 dp
makes the fingerprint fail on any retrain. That may be tighter than useful,
but loosening a regression guard has its own risks and is not a change to
make while using it.*

**Still open — needs data and a decision, not code:** replacing the target
requires accumulated `interaction_events` and `recommendation_log` rows
(F-42, now filling) *and* a product decision about what a good recommendation
is: engagement, completion, explicit rating, or return visits. Options will be
written up rather than chosen unilaterally.

---

#### DECISION (product owner, 2026-09-11): the relevance target is **reading depth**

> *"It's buildable now, and it measures what the product actually claims to
> care about — not just curiosity-driven clicks."*

This answers F-26's blocking question. The circular popularity target is
replaced by **how far readers get into a book**, normalised and aggregated per
book across everyone who engaged with it.

**Why reading depth, over the alternatives — decided on what exists, not what
is theoretically possible.** An audit of what the product can actually
capture found:

| Signal | State on 2026-09-11 |
|---|---|
| explicit ratings | two mechanisms in the schema (`comments.rating`, `POST /api/feedback`); neither reachable — posting needs a login the UI does not have |
| completion / progress | `reading_progress` exists; **no page calls `POST /api/progress`**, and it needs a login |
| click-through | impossible: the UI's recommendation path (`/api/questionnaire`) logs no impressions, and there is **no visitor identity** to join a click to an impression |
| **reading depth** | **the reader already requests one page per turn** (`/pages?page=N&page_size=1`) — the signal exists in the traffic, only unlogged |

Reading depth was the only candidate capturable today with no login and no
new UI. It is also the closest to the product's own thesis (§6): *the system
should become more useful as the user actually reads.* A click measures
curiosity about a title; pages read measure whether the recommendation was
right.

**Scope, stated plainly.** This is **book-level relevance** — which
recommendations work *in general*. It is not personalisation. With no login
and no visitor id, a page turn cannot be attributed to a person, so the model
learns what holds readers, not what holds *this* reader. Expected and accepted
until login exists.

**Two refinements made during implementation, recorded rather than silent:**

1. **Normalised by excerpt length, not book length.** `book_texts` holds the
   opening chapters only — measured at 20,358 characters on average, about 11
   pages at 1,800 per page. Against a 300-page book a reader who finishes
   every available page scores ~4%, which would compress every book into a
   sliver of the range. Against the excerpt, reading all of it is 1.0 and
   bouncing after page one is 0.0. So the honest description of what is
   measured is **"did the opening of a recommended book hold the reader"** —
   whole-book depth needs whole-book text, which is Phase 5.

2. **Empty pages do not count.** The reader's pager uses the book's full page
   count (`currentBook.pages || 100`), so it lets a reader click "next" to
   page 300 through an 11-page excerpt. Each of those empty pages would
   otherwise log as reading deeper. Only pages that returned content count.

**Login shipped (OI-6, 2026-09-17).** Comments, reminders and progress are
now reachable through the UI with a real account.

**Per-reader page-turn attribution shipped (OI-6 follow-through, 2026-09-17.)**
`_record_page_turn` (`api/books.py`) was scoped and reported on before
writing anything, since "wire up per-reader depth attribution" could mean
either a small, contained data-plumbing step or something that fans out into
ranking/ML work. The decomposition held up under investigation:

* **Attributing the event is small and schema-ready.** `interaction_events
.user_id` has been nullable, FK'd to `users`, and indexed since PR 2 —
  no migration needed. `/api/books/{book_id}/pages` now takes `OptionalUser`
  (it did not take *any* identity parameter before, not even optionally) and
  threads `user.id` into `_record_page_turn`, the same shape `book_detail`'s
  `BOOK_VIEW` event already used one function above it. Anonymous reading is
  unaffected — `OptionalUser` tolerates both, and an anonymous page turn
  still records `user_id=None`, which remains meaningful signal, not a gap.
* **Using per-reader depth as a ranking feature is a different, larger
  problem, and was explicitly declined for now.** `reading_depth`/
  `reading_depth_n` are the LTR's training *label*, not a feature —
  `test_depth_never_reaches_the_feature_matrix` exists specifically to keep
  it that way, because a column that is both label and input would let the
  model learn nothing but "relevance equals this column." A per-reader
  ranking signal needs a genuinely new mechanism (a request-time join of a
  user's history into candidate scoring), not a tweak to `_relevance()`, and
  was scoped as its own future decision rather than folded in here.
* **"Reading DNA (§25)"**, the other unlock this table names, was checked
  against the rest of this document and found to have no specification
  beyond this one row — flagged rather than guessed at. Logged as OI-12
  below: needs a product decision before it is buildable, the same
  treatment F-26 got before reading depth itself was decided.

**Decided alongside: also build a "continue reading" UI (the natural first
consumer of attribution that touches neither ranking nor ML), and also wire
up `POST /api/progress`.** The second was a related, adjacent gap found
during scoping: fully built and authenticated server-side since PR 2/F-12,
with zero frontend callers anywhere in the codebase.

What shipped:

* `frontend/user.html`'s `loadPage()` now calls `AUTH.authFetch` instead of
  plain `fetch` (attaches the token when one exists, identical behaviour
  otherwise), and posts to `POST /api/progress` after every successful page
  load when logged in.
* A "Continue Reading" card in the workspace sidebar, populated from
  `GET /api/progress`, hidden entirely for anonymous visitors or when
  nothing is in progress — reading has never required an account and this
  does not change that. Clicking an entry resumes at the saved page via a
  new `startPage` parameter on `loadBook()`.
* `_progress_payload` (`api/reading.py`) enriched with `title`/`author`/
  `thumbnail` — the same pattern `_reminder_payload` already used — since
  the continue-reading list renders this response directly and a bare
  `book_id` is not something a UI can show. `store.progress_for_user` now
  orders by `updated_at desc`, since nothing previously cared about order
  and a reader expects their most recent book first.

**A real, live bug found while wiring the first real caller of
`POST /api/progress`, not a hypothetical.** `page` was being stored as
`int(payload.total_pages or 0)` — the book's page *count* written verbatim
into the column meant to hold the page *reached*. Concretely:
`progress=0.5, total_pages=200` stored `page=200` (the endpoint) instead of
100 (where the reader actually was), and the exact shape
`test_progress_is_scoped_to_the_caller` already sent —
`{"book_id": 1, "progress": 0.5}`, no `total_pages` — stored a flat `page=0`
regardless of progress. Invisible until now because nothing read `row.page`
back; the continue-reading feature is the first thing that does. Fixed to
derive it: `page = round(progress * total_pages)`.

Guarded by four new tests in `tests/test_auth.py`
(`test_a_page_turn_is_attributed_to_the_logged_in_reader`,
`test_an_anonymous_page_turn_still_records_no_user`,
`test_progress_page_is_derived_from_the_fraction_not_the_page_count`,
`test_progress_response_is_enriched_with_book_details`), each sabotage-
checked by reverting the fix and confirming the matching test fails and
nothing else does. Verified live end to end against the real backend and
real Postgres — not just the suite — before and after: register → read a
page while authenticated → confirm the `interaction_events` row carries the
real `user_id` → post progress at `progress=0.5, total_pages=200` → confirm
the stored `page` is 100, not 200 or 0 → `GET /api/progress` returns the
enriched, title-bearing list — then the test account and its rows deleted
from `digikitab` afterward, the same discipline as OI-6 itself.

Full suite: 317 passed, 3 xfailed (was 313 before this — 4 new tests, all
passing). Golden ranking baselines untouched, as expected: nothing about
book-level aggregation changed.

### OI-12 · Reading DNA (§25) — OPEN, **NEEDS A PRODUCT DECISION**

Named in F-26's original "unlocked by login" table (2026-09-11) as one of
two things OI-6 would make reachable. The other, per-reader depth
attribution, is done (above). This one is not, because it turned out to
have no specification anywhere in this document beyond that single table
row — no definition of what "Reading DNA" actually is, what data it draws
on beyond reading depth, what it outputs, or where a reader would see it.

**Same shape as F-26 before 2026-09-11's decision: logged rather than
guessed at.** Building a placeholder version would repeat exactly the
mistake F-26 spent most of Phase 3 correcting — a feature that superficially
works but is built on an undefined target, discovered to be hollow only
after real use. The data it would need now exists (per-reader page-turn
attribution, per-reader progress, comments, ratings), so this is no longer
blocked on data — only on the product owner defining what it is.

**Not started, by the product owner's instruction (2026-09-17).** Do not
begin without a specification to build against.



### F-24 · The global Python is shared — **CLOSED**, status corrected 2026-09-26
Installing the pinned requirements downgraded `click` and broke an unrelated
`huggingface-hub`. Repaired, but the project should not be installing into a
shared interpreter. A `.venv` and a note in the README is a five-minute fix
worth doing in PR 2.

**Already done, just never marked so — the exact stale-status pattern this
file keeps catching itself in.** `.venv` has existed and run the full stack
since well before this entry was found still open; the README's own text
(line 447 of an earlier draft of this file) says outright "the venv also
runs the full suite green... which incidentally closes F-24." Found while
doing something else entirely (checking this table for quick, safe wins)
and re-verified directly rather than trusted: `.venv/Scripts/python.exe`
exists, and `README.md`'s Setup section already instructs a venv before
`pip install`. Closing the status, not the work — the work was done.


## F-62 · The golden drift is not F-51, and it is wider than three tests — **RESOLVED, see F-63/F-64** (2026-09-23)

Investigated per the handoff's §4. The prescribed experiment returned the
**opposite** of the expected answer, so the question the handoff set out to
close is reopened rather than settled.

### The prescribed experiment, and what it said

`book_vector_pass --limit 40000 --redo` was run against `digikitab_test` a
third time. The vectors provably changed — `md5(string_agg(embedding))` over
all 29,975 rows went `7aeb67a278b75cac484c81d680dc7f93` →
`4f6784f2978e397ac0d4c05e3bba7775`. The three failing cases then failed with
**identical** values to before the redo (0.5718 / 0.4906 / 0.6426).

The handoff's own decision rule was: *different* values confirm F-51, *same*
values mean F-51 is not the explanation. They were the same. **F-51 is ruled
out as the cause of this drift.**

### F-51's mechanism is real, and is now measured — but it is 1e-7, not 1e-4

Done directly, as F-51's own remediation note asks ("compare raw MiniLM
embedding output across two fresh fits on identical input, bit for bit"):

| Comparison | Result |
|---|---|
| Two `encode()` calls, same process, same batch | **bit-identical** |
| Same text alone vs inside a batch, same process | differs, max abs 1.1e-7 |
| Stored vector (2026-09-22 fit) vs fresh encode | differs, max abs 1.1e-7, cos 1.0000001 |

So embeddings are genuinely not bit-reproducible across fits, and **batch
composition is a demonstrable cause** — that part of F-51 is confirmed and no
longer needs isolating. But the noise is ~1e-7 and the golden scores are
compared at 1e-4, and the full-catalogue redo above shows it does not
propagate that far. F-51 is real and orthogonal, not the culprit here.

### The drift is deterministic, so it is not nondeterminism at all

Three separate processes — three full ML refits — produced identical scores,
before and after the vector regeneration. Whatever moved these numbers is
persistent state, not float noise.

### It is six baselines, not three, and one is a real ranking change

`pytest` asserts title-then-score per rank and stops at the first mismatch in
a test, so it reports only the earliest drifted rank and hides the rest. A
direct comparison of every golden file at every rank — including the ranks
`MAX_SAFE_PREFIX` truncates — gives the actual state:

| Baseline | Last written | Drift |
|---|---|---|
| recommend_fiction_dark | 7e5a57e 09-11 | 0.0000 |
| recommend_fantasy_adventurous | 7e5a57e 09-11 | 0.0000 |
| questionnaire_fantasy_dark_fast | 7e5a57e 09-11 | 0.0000 |
| questionnaire_selfhelp_motivational | 7e5a57e 09-11 | 0.0001 (2 ranks) |
| recommend_no_preferences | 538e69e 09-14 | 0.0001 (6 ranks) |
| recommend_thoughtful_mood | 538e69e 09-14 | 0.0001 (8 ranks) |
| questionnaire_popular_english | 538e69e 09-14 | 0.0001 (6 ranks) |
| questionnaire_empty_answers | 538e69e 09-14 | 0.0001 (**all 9 ranks**) |
| **recommend_author_only** | 538e69e 09-14 | **0.0006, and ranks 6/7 swapped** |

`recommend_author_only` returns *Warbreaker* and *The Final Empire* in the
opposite order from the baseline. That is a ranking change, not score noise,
and the handoff's "non-rank-1 score drifts" framing does not cover it.

**The tolerance is the same order as the drift.** `SCORE_TOLERANCE = 1e-4`
and the systematic shift is 1e-4, so five of the six moved baselines pass
while being wrong. The suite is currently green on a shift it is too coarse
to resolve — the F-43/F-45 family again: a pass that is not evidence.

### What was ruled out, by measurement

| Candidate | Evidence it is not the cause |
|---|---|
| F-51 / GPU float noise | full `--redo`, checksum changed, scores identical |
| Phase 4 slice 1 code | `catalogue_only()` is `owner_id IS NULL`; all 29,975 rows match, so a no-op here |
| Any committed code since the baselines | `git diff 538e69e..HEAD` over the whole scoring path is renames (F-14), dead-code removal (F-49), docstrings. `ml/ranking.py` is a pure `cf_s`→`genre_pop_s` rename |
| Library versions | nothing in `.venv` installed after 2026-09-11; baselines written 09-14 |
| The catalogue file | `backend/site_ready_books.json` unmodified vs HEAD, mtime 2026-04-25 |
| `comment_score` | `comments` is empty in `digikitab_test`; score is 0 for every book |
| Bandit | no rehydration anywhere; `expected_reward` returns the constant 0.5 with no rewards |
| Lost descriptions | `digikitab_test` has 0 and that is *correct* — F-15's placeholder is nulled by `clean_description`. Running the same comparison against `digikitab` (which has 7,965) changes whole title lists, not fourth decimals, so a description change cannot produce this signature |
| Vector recipe | `build_text` unchanged since before the baselines |
| Vector→row mapping | 29,975 rows, 29,975 distinct `(source, external_id)`; the 1,576 colliding `book_id`s are correctly disambiguated by source (F-27). No missing vectors, no TF-IDF fallback |

### What is left, and the part that is inference

Every reproducible input is eliminated. The remaining difference is the
fixture itself: `digikitab_test` was destroyed and rebuilt on 2026-09-22
(F-59's `prune()`, then F-61's `TRUNCATE ... CASCADE`), and restored by
re-running `ingest` + `book_vector_pass`. It is now in **pure fresh-ingest
state** — its `title`/`author`/`genre` match the catalogue JSONL exactly,
with no accumulated pass output on top.

The baselines were written on 2026-09-14, against the *pre-wipe* database,
which had months of passes applied to it. That state no longer exists and
cannot be reconstructed, so this last step is inference, not measurement —
but it is the only surviving candidate, and there is direct corroboration
that the restore was **not** faithful:

```
                 digikitab (dev)   digikitab_test
book_texts             6,305              0
book_chunks          153,711              0
book_vectors          29,975         29,975
```

`book_texts` and `book_chunks` were taken by the same cascade and **have
never been restored**. The restore rebuilt `books` and `book_vectors` only.
So the test fixture is still missing data it had before 2026-09-22, and the
baselines are being compared against a fixture that is not the one they were
recorded in.

### Consequences worth separating

1. **The three failures are not a Phase 4 regression.** Slice 1's code is
   exonerated by measurement, not by assumption.
2. **The golden baselines no longer describe the current fixture**, and
   cannot be made to, because the state they encode is gone.
3. **`book_texts`/`book_chunks` are still empty in `digikitab_test`** — an
   unrepaired gap from the 2026-09-22 incident, independent of the golden
   question and affecting anything that reads chunks.
4. **`SCORE_TOLERANCE` is too coarse to see the drift it is meant to catch**,
   and `MAX_SAFE_PREFIX` hides further ranks on top of that.

### The decision needed

Not the handoff's (a)/(b)/(c), which all assumed F-51. The real choice is
what a golden baseline should be pinned against now that the fixture is
known to have been rebuilt — and whether `book_texts`/`book_chunks` are
restored first, since regenerating baselines before that repair would pin a
second state that is also about to change. **Nothing committed pending this.**

**Decided 2026-09-23 (product owner): restore `book_texts`/`book_chunks`
first, verify the test DB fully matches dev in structure and content, then
regenerate baselines against that complete state — not before.** See F-63.

---

## F-63 · `book_texts`/`book_chunks` restored into `digikitab_test`; the restore verification found the tolerance question is more urgent than expected, plus 3 masked test-isolation gaps — 2026-09-23

Closes the F-62 restore step. Golden baselines are still **not** touched —
that is the next, separate step, still pending.

### The restore

`digikitab_test` lost `book_texts` and `book_chunks` in the 2026-09-22
`TRUNCATE ... CASCADE` incident (F-61) and the restore that followed only
rebuilt `books` + `book_vectors` (F-59). Dev and test have disjoint primary
keys (`books.id` ranges `1..53595` vs `88896..118870`), so nothing could be
copied by id — every row was carried across on `(source, external_id)`,
verified to be a collision-free 1:1 mapping before any write (6,305 dev keys,
6,305 matched in test, 0 unmatched, 0 duplicate targets).

1. `book_texts`: exported from dev keyed on `(source, external_id)`, inserted
   into test remapped to test's own `book_id`. **6,305 rows, byte-identical**
   — `md5(string_agg(... md5(content) ...))` over the full table matches
   between dev and test exactly.
2. `book_chunks`: re-derived in test via `chunk_pass --origin text` (chunks
   are not raw data, they are a deterministic function of `book_texts`, so
   re-running the pass is the correct restore, not a copy). **145,887 chunks
   from 6,304 books.** Checked key-for-key against dev's 144,081 chunks
   (`(source, external_id, ordinal)`): all 144,081 present, **0 content
   mismatches**. The extra 1,806 chunks belong to 80 books dev had not yet
   chunked (dev carries its own 81-book backlog, confirmed independently) —
   test is now slightly *ahead* of dev, not divergent from it.
3. Embedded via `embed_pass --backend minilm`: **145,887/145,887, 100%
   coverage**, matching dev's embedding space.
4. Schema and migration state: `information_schema.columns` diff between
   dev and test is empty; both at `alembic_version = 7bf266537994`.

Golden ranking suite re-run after the full restore: **identical result**
(same 3 failing cases, same values, 15 passed, 2 xfailed) — confirms neither
`book_texts` nor `book_chunks` feeds `Recommender`/`QuestionerEngine`, so the
restore could not have been expected to move F-62's numbers, and didn't.

### The tolerance question the product owner asked to log separately

Measured directly rather than guessed. `_compare`'s per-row loop checks
title equality *before* the score, so in principle a ranking swap is always
caught — the risk is whether the score assertion on an *earlier*, correctly-
ordered row fails first and hides it. Replayed `recommend_author_only`'s full
measured row set (§F-62) through `_compare`'s exact logic at several
`abs_tol` values:

| `abs_tol` | first assertion the reader sees |
|---|---|
| 1e-5, **1e-4 (current)** | rank 2: "score drifted" |
| 2e-4, 5e-4 | rank 5: "score drifted" |
| 1e-3, 1e-2 | rank 6: **"ranking changed"** — the real defect |

**Tightening `SCORE_TOLERANCE` does not help and would not have caught this
sooner — it is already tight enough to fail first.** The rank 6/7 swap is
real and current at any tolerance from 1e-5 up to just under 1e-3; it is
masked only because a *smaller*, less interesting drift earlier in the same
list trips the assertion first and `pytest` stops there. The fix this points
to is in `_compare`'s control flow, not the constant: check every row's
title before checking any row's score (or report all mismatches instead of
the first), so a real ranking change is never hidden behind an earlier
cosmetic one. Not built — this is a test-harness change adjacent to the
open F-62 decision, not part of the restore. Flagged for the product owner
rather than done unilaterally.

### Collateral: 3 tests were passing vacuously against an empty table

Running the full suite after the restore surfaced 3 new failures, all
pre-existing test-isolation gaps that the 2026-09-22 data loss had been
silently hiding — not caused by the restore, not data corruption, not
production bugs:

- `test_search.py::test_nonsense_query_returns_nothing_rather_than_a_bad_guess`
  asserts a nonsense query returns `[]`, but calls `search_chunks` with no
  `embedding_model` filter and no scope to its own fixture's book. Against
  an empty/near-empty `book_chunks` that was trivially true. Against the
  real 145,887-chunk corpus, a nonsense query legitimately has *some* chunk
  above `min_similarity=0.05` somewhere in the whole catalogue. The
  production caller (`api/search.py:130`) already passes
  `embedding_model=encoder.name` explicitly, with a comment naming this
  exact hazard — **the app is not affected, only this test's setup is.**
- `test_embed_pass.py::test_a_chunk_from_another_backend_is_re_embedded` and
  `::test_a_chunk_already_on_the_current_backend_is_left_alone` both call
  `embed_pass.run(limit=5000, redo=False, backend_name="hashing")` and
  assert against one fixture-created chunk. With the table empty that chunk
  was the only pending row within `limit=5000`. With 145,887 real chunks
  (all `embedding_model='minilm'`, none `'hashing'`), the *entire corpus*
  now reads as pending for backend `hashing`, so `limit=5000` picks up 5,000
  arbitrary real chunks — possibly not including the fixture's row (test 1),
  and definitely leaving thousands more pending for a second run to pick up
  (test 2, "re-embedded 5000 chunk(s) that were already current").

Same family as F-59/F-61's lesson, generalised: **a test may assert against
a shared table's global state only if it scopes the query to rows it
created itself.** All three assume the table is empty apart from their own
fixture, which was true by accident (data loss) rather than by design.
Not fixed here — flagged rather than silently expanded into scope the
product owner did not ask for this pass. `docs/PROGRESS.md` housekeeping
note applies: worth fixing before anyone next relies on these three tests
meaning what they say.

**Net suite state after the restore, before touching goldens:** the same 3
golden failures as F-62, plus these 3 newly-visible (not newly-caused)
isolation gaps. Full numbers in the next full-suite run are the ones to cite,
not this table — re-run before reporting a final count.

**Decided 2026-09-24 (product owner): fix the isolation gaps first, then
regenerate baselines once — not before, to avoid touching them twice.** See
F-64 for what "fixing the isolation gaps" actually turned out to require.

---

## F-64 · The 3 "isolation gaps" were one systemic bug: an ORDER-BY-less query was silently deleting a real book's chunks on every affected test run — 2026-09-24

Closes the F-63 follow-up. Found while fixing the 3 tests flagged in F-63,
before regenerating any baseline (still untouched — that is the next step).

### What F-63 missed

Re-running the F-63 group to fix its 3 known failures surfaced a 4th,
more serious problem: `book_chunks` dropped from 145,887 to 145,866 rows
— **21 real chunks silently and permanently deleted** — after nothing more
than running `tests/test_search.py` on its own. Traced to the exact book by
diffing dev's chunk keys against test's (same method as F-63's restore
verification): Gutenberg's *David Copperfield* (external_id `766`, book_id
in this database), fully chunked (21 chunks) after the F-63 restore, ended
the run with zero.

### Root cause

`corpus`, `book_vector_corpus` (`test_search.py`), `seeded`
(`test_search_endpoint.py`) and `sample_book` (`test_chunk_pass.py`) all pick
a "test book" the same way: `select(Book).limit(1)` (or `.limit(2)`), **no
`ORDER BY`.** Every one of them assumed this returns some arbitrary,
content-free row, and each deletes that book's `book_chunks` rows to make
room for its own fixture data.

That assumption was true only by the accident of F-63's restore state, not
by anything the query guarantees. Postgres has never promised row order
without `ORDER BY`; it returns physical scan order, which this table's
restore/rebuild history (F-59, F-61, F-63) has no reason to leave correlated
with ascending `id`. Measured directly: `select(Book).limit(1)` on this
database returns *David Copperfield* — a real, fully-chunked book — not the
lowest-`id` row (verified separately at `id=88896`, an unrelated chunkless
book). Every fixture's assumption was wrong for the same reason at the same
time, which is why it reads as one bug wearing four costumes rather than
four unrelated ones.

**The telling detail: three of these four fixtures already do the right
thing for `BookVector`** — snapshot the real row before deleting it, restore
it in teardown — with a comment explaining *why* (`load_content_vectors` is
all-or-nothing, so leaving a gap breaks the golden suite). `book_chunks` got
no such discipline because nothing was known to break from leaving it empty.
Nothing does break, functionally — F-63 already confirmed the golden suite
doesn't read `book_chunks` — but "the golden suite doesn't notice" and "the
data isn't destroyed" are different claims, and the fixture docstrings had
quietly conflated them. `book_vector_corpus`'s own comment said the quiet
part directly: *"a delete-and-leave-empty teardown \[...\] safely, since
nothing reads 'all chunks exist'"* — true of the suite, false of the
reader who uploaded that content and just lost it from search.

Same family as F-59/F-61 again, one level more general: **a test may mutate
a shared table's row only if it knows, not assumes, what was there first.**
`select(Book).limit(N)` with no `ORDER BY` cannot supply that knowledge.

### Fix

All four fixtures now snapshot the real `BookChunk` rows for their picked
book(s) before deleting, and restore them in teardown — the identical
discipline already applied to `BookVector` in the same fixtures, extended to
the table that didn't have it. No fixture's actual test behaviour changed;
only its cleanup got honest about what it owns.

Two smaller companions, from F-63's original list, fixed alongside:

- `test_nonsense_query_returns_nothing_rather_than_a_bad_guess`
  (`test_search.py`): `search_chunks` has no way to scope a query to one
  book, so the assertion is now scoped client-side to the fixture's own
  `book_id` rather than asserting emptiness across the whole shared corpus.
- `test_embed_pass.py`'s two backend-mismatch tests: `embed_pass.run()`
  gained an optional `book_ids: list[int] | None = None` parameter
  (`backend/scripts/embed_pass.py`) — `None` preserves every existing
  caller's behaviour unchanged. Without it, these tests had no way to run
  `embed_pass.run(backend_name="hashing")` against a real, populated
  `book_chunks` table without also re-embedding thousands of unrelated real
  rows into a throwaway backend name. **This is not hypothetical: it is what
  actually corrupted 60,000 of 145,887 real chunk embeddings during F-63's
  own verification work**, repaired twice via `embed_pass --backend minilm
  --redo` before this fix, described here rather than in F-63 because the
  fix belongs with its root cause.

### Verification

- Sabotage-style, by reproduction before the fix: same 6-file group,
  identical 21-chunk loss, same book, twice — confirmed deterministic, not
  a fluke.
- After the fix, the same group leaves `book_chunks` at 145,887 with 0 rows
  on any non-`minilm` embedding model, and `test_embed_pass.py` (4/4),
  `test_search.py`'s nonsense-query test, and the three fixture-owning
  files all pass.
- **Two consecutive full-suite runs, identical:** 472 passed / 3 failed / 2
  xfailed both times — the 3 failures are exactly F-62's known goldens, same
  values both runs. `book_chunks` held at 145,887 rows / 0 corrupted through
  both. This is the same 472/3/2 split the project was at before F-59/F-61
  ever happened, now reached with the restore verified rather than assumed.
- Content re-verified once more against dev after all of the above: 0
  mismatches, 0 missing, `book_texts` checksum unchanged from F-63.

One incidental infrastructure event during this work, unrelated to the
above: both Docker containers exited cleanly mid-run (OI-8's known
sleep/wake pattern), producing 9 transient `test_upload_isolation.py`
errors in one run. Restarted from the correct directory (containers
reattached to the pinned `final_*` volumes, per F-56 — no new volume
created), data confirmed intact, and the affected file re-run clean
(10/10) before trusting any further result.

### Baselines regenerated — 2026-09-24, closing F-62

With the restore verified and both isolation-gap fix runs clean, the 3
baselines F-62 identified as genuinely drifted were regenerated —
`recommend_author_only`, `recommend_thoughtful_mood`,
`questionnaire_empty_answers` — and only those three; the other 6 golden
files are untouched (`git status` confirms it). Each diff was read by hand
before anything was committed:

- `questionnaire_empty_answers`, `recommend_thoughtful_mood`: uniform
  **-0.0001** at every rank, no title or order change — the "score drifted"
  shape F-62 measured.
- `recommend_author_only`: same pattern at ranks 2–5 and 8–9, plus the
  ranks-6/7 title swap (*The Final Empire* ↔ *Warbreaker*) F-62 flagged as a
  real ranking change, not scoring noise. Measured the unrounded scores
  underneath before accepting the diff: **5.27e-06** separates the two
  books at full precision — a genuine but extremely close near-tie that
  happened to cross a rounding boundary at the 4th decimal the API reports.

Golden ranking suite: **18 passed, 2 xfailed** (was 15 passed / 3 failed / 2
xfailed before regeneration). Two consecutive full-suite runs after
regenerating: **475 passed, 2 xfailed, 0 failed**, identical both times.
`book_chunks` held at 145,887 rows / 0 corrupted through both.

F-62 is closed. F-51 remains open, narrowed (F-64's investigation
independently confirmed its 1e-7-scale embedding non-reproducibility, see
F-62's own writeup) but not the cause of anything found here.

---

## Phase 4 slice 2a · Upload endpoint + attestation — shipped 2026-09-25

`POST /api/library/upload` and `GET /api/library`, per section 29's format
list and section 30's attestation requirement, and the handoff's agreed
order: this is only the upload endpoint itself. Extraction (TXT+EPUB, PDF
deferred), the background job to run it, and private search wired into the
AI Librarian are the next three slices, in that order, untouched here.

**Schema** (migration `c25d1ebf747d`, applied and round-tripped on both
databases): six nullable `books` columns — `upload_status`,
`source_filename`, `file_format`, `storage_path`, `file_size_bytes`,
`attested_at`, `attestation_version` — NULL for every catalogue row,
following `owner_id`'s own precedent of one table over a second `uploads`
table with its own authorization surface to get wrong.

**Attestation (section 30), decided with the product owner rather than
assumed:** a required boolean per upload, not implicit consent and not
purchase-verification infrastructure — `attests_ownership` must be
explicitly true, rejected before the file is even read otherwise, recorded
with a timestamp and a version string (`attestation_version`) so a later
change to the wording cannot retroactively reinterpret an attestation
someone already made. No ISBN/receipt/invoice capture in this slice; that
is section 30's separate `OWNED_NOT_UPLOADED` future state, left alone.

**Format validation is extension-first, content-checked**, not a full
parser: `.txt` must decode as UTF-8, `.epub` must be a valid zip whose
`mimetype` entry (when present) reads `application/epub+zip`, checked by
`file_size` before decompressing it — an oversized declared size is refused
without reading it, since `zf.read` decompresses whatever the archive
claims and a crafted entry could otherwise expand to gigabytes inside one
request. Fully validating EPUB structure is extraction's job, a later
slice. **PDF is deliberately rejected for now**, not queued: section 29
lists it as a supported format, but its extraction pass does not exist yet,
and accepting it would mean a file sitting at `upload_status="uploaded"`
indefinitely with nothing telling the reader that nothing is coming — the
same "honest not-yet beats a silent stall" reasoning as F-17 and F-53. One
line to remove once PDF extraction ships.

**Storage is never influenced by caller input.** The file on disk is named
`{server-generated uuid}.{validated extension}`; `source_filename` keeps
the caller's original name for display only. This project has already paid
once for the alternative (F-04/F-05, arbitrary file overwrite and path
traversal, a different feature) — `test_uploaded_filename_never_becomes_a_filesystem_path`
asserts the actual contract (the stored name is exactly
`{external_id}.{ext}`, not merely "contains no `..`") and was sabotage-verified:
reverting `disk_filename` to derive from the caller's filename (even via a
safe `.name` basename, not a raw path-traversal payload) fails it.

**Bounded before it is a problem, not after:** `MAX_UPLOAD_BYTES` (50 MB)
is enforced by reading at most one byte past the limit
(`file.file.read(MAX_UPLOAD_BYTES + 1)`), not by reading the whole upload
into memory first and checking after — the difference between capping disk
and capping memory. The route is a plain `def`, not `async def`, so
FastAPI runs the synchronous disk write, zip parse and DB commit in the
threadpool rather than blocking the event loop for every other request
while one upload is processed. Upload is rate-limited the same way
audiobook generation and chat already are (`ratelimit.hit_all`,
`UPLOAD_LIMIT = 10` per hour, both IP and account bucket) — authenticated
rather than anonymous, so the threat here is a careless or scripted caller
filling the disk, not an anonymous flood, but the existing utility already
does exactly what that needs.

**Verified:** `tests/test_upload_endpoint.py`, 15 tests — auth, attestation
(present and required-true), both supported formats, the PDF deferral, four
content-mismatch cases (binary-as-txt, non-zip-as-epub, wrong-mimetype-as-epub,
empty file), the oversize limit, the path-traversal contract, catalogue
isolation (`catalogue_only` correctly excludes an upload), cross-reader
listing isolation, and the rate limit. Four sabotaged deliberately
(path-generation, PDF-deferral, attestation, `owner_id` assignment) to
confirm the tests actually fail without the code they check — the
PDF-deferral test failed to catch its own sabotage on the first pass (the
generic "unsupported format" rejection also echoes the extension, so
`"pdf" in message` passed either way) and was sharpened to check for
deferral-specific language and the *absence* of "unsupported", then
re-sabotaged to confirm the sharpened version does fail. The other three
caught their sabotage on the first attempt.

One test-contamination incident during this verification, caught and
cleaned before committing: the `owner_id` sabotage run left 3 rows with
`source='upload', owner_id IS NULL` in `digikitab_test` (the cleanup
fixture keys its own teardown on `owner_id == user_id`, so it correctly
found nothing to clean up for rows the sabotage had orphaned) plus their
3 files under `backend/uploads/`. Both removed by hand; `books` count
reconfirmed at 29,975 before the final suite runs.

Two consecutive full-suite runs after fixing and cleaning up: 490 passed,
2 xfailed, 0 failed, identical both times (475 + this slice's 15 new
tests). `book_chunks` held at 145,887 rows / 0 corrupted through both.

Dependency added: `python-multipart==0.0.32` (FastAPI's `File`/`Form`
require it; not previously installed).

---

## Phase 4 slices 2b-4: extraction, background ingest, private Librarian search — 2026-09-25

Built end to end in one autonomous stretch (product owner authorized
proceeding through slice 5 without per-slice sign-off; stop conditions were
irreversible/destructive actions, a real product decision, or GPU-thermal
risk — none were hit). Decisions below are logged for review, not for
approval already granted.

### Extraction (`services/extraction.py`)

TXT: decode as UTF-8 (already validated at upload). EPUB: `ebooklib` +
`beautifulsoup4` (new dependencies — no existing HTML/EPUB parser in this
project, and hand-rolling OPF/spine/manifest parsing correctly was judged
not worth it against a mature, standard library for exactly this job).

**Deliberately unbounded**, unlike the catalogue's Gutenberg excerpt
(`extract_reading_text`, capped ~20K chars — a taste of a public-domain book
shared across 29,975 rows, where storing everything in full would cost
~3 GB). An upload is the reader's own single book: storage for the whole
thing is negligible, and section 29's entire point is a private RAG
assistant grounded in *all* of it. Truncating an upload the same way the
catalogue is truncated would have quietly broken the feature's own reason
to exist — the Librarian would answer confidently from the first ~11 pages
and never reach the back half of anyone's book. Judgment call, not asked
about: the two truncation policies solve different problems for different
reasons, and applying the catalogue's for cost-avoidance reasons that do
not exist for a single private file would have been copying a number, not
the reasoning behind it.

**Found before it shipped**: naive EPUB spine iteration includes the
navigation document — same `ITEM_DOCUMENT` type as a real chapter — which
would have prepended a page of chapter titles to every extraction, the
exact "table of contents indexed as though it were the book" mistake F-40
already found and fixed for Gutenberg text, in a different format.
`EpubHtml.is_chapter()` is ebooklib's own distinction for this (verified
against the library's source: `EpubNav` overrides it to `False`, the NCX
item is not an `EpubHtml` subclass at all), not something inferred from one
test EPUB.

### `chunk_one` extended, not duplicated (`scripts/chunk_pass.py`)

Added `user_id: int | None = None` (default preserves every existing
caller exactly). `None` writes public/ownerless, as before; a real id
writes `visibility="private"` for that owner. One function both paths go
through, matching the reasoning `book_chunks` itself is built on (one
table, two populations) applied to the code that populates it, rather than
a second, parallel chunking implementation for uploads that could drift
from the first.

### Background ingest job (`core/jobs.py`, `services/library_ingest.py`)

`JobRegistry` gained `timeout`/`label` parameters (both optional,
backward-compatible) so a second instance — `LIBRARY_REGISTRY`, 600s,
separate from `REGISTRY`'s 120s — could exist without widening the
audiobook registry's timeout for every job needlessly. Separate instances
rather than a per-call timeout override: an ingest job that ran long must
not occupy a worker slot audiobook generation is also waiting on.

**Caught by running the existing suite before trusting the change** (the
standing method's own point): the first version captured `JOB_TIMEOUT_SECONDS`
into the instance at construction time, which broke
`test_a_wedged_job_is_reported_failed_rather_than_running_forever` —
that test constructs a registry and *then* monkeypatches the module
constant, which only a live read honours. Fixed with a `None`-sentinel
default (`self._timeout = None` means "track the module constant live",
an explicit value means "this registry's own, fixed") rather than loosening
the test.

`process_upload(book_id, uploads_dir)` runs Extract -> Chunk -> Embed,
advancing `book.upload_status` and committing before each next step — a
failure partway through leaves an honest, resumable record of how far it
got (`extraction_failed` / `chunking_failed` / `embedding_failed`), the
same discipline `enrichment_status`/`is_complete` already apply elsewhere
(F-17). Embedding reuses `embed_pass.run(book_ids=[book_id])` — the exact
scoping parameter F-64 added earlier in this same stretch, now load-bearing
for a second reason.

**Bug found by the pipeline's own tests, not in production**: `upload_status`
was `VARCHAR(16)`; `"extraction_failed"` is 17 characters. Postgres raised
`StringDataRightTruncation` rather than silently truncating it — correct
behaviour on the database's part, but a column sized without checking its
own vocabulary. Widened to `VARCHAR(32)` (migration `a73fc73a4646`, applied
and round-tripped on both databases) for headroom as the pipeline's
vocabulary keeps growing, rather than a second one-character-margin column
needing widening again next time.

Upload now triggers the job automatically and returns `job_id`/`poll` in
the same response — still 200 (the upload itself succeeded; processing is
separate and asynchronous). A saturated ingest queue degrades rather than
fails the request: the file is already stored and durable, only processing
is delayed.

**Found, then fixed, not deferred (separate commit,
`ml/embeddings.py`):** what started as a "noted, not fixed" observation
escalated to a real, reproduced test failure — `SentenceTransformerBackend`
did not set `local_files_only`, so every embed call — now including every
upload's ingest job, not just the periodic maintenance passes — made a
network round-trip to huggingface.co to check for model updates before
using the local cache. A degraded connection during this stretch's own test
runs (`WinError 10060`, a genuine timeout, confirmed transient by a direct
`curl` immediately after) turned that into 6 failing tests in
`test_upload_endpoint.py`, all in the embedding step, all timing out inside
`wait_for_job`'s 30s window while the retry loop worked through several
per-file HEAD requests.

First attempt — `HF_HUB_OFFLINE=1` before constructing `SentenceTransformer`
— measured and found ineffective: every one of the library's own per-file
network checks fired regardless. `SentenceTransformer.__init__`'s own
`local_files_only=True` parameter does what the env var did not: verified
directly, it took model load from ~40s with a full page of HTTP requests to
~4s with none. Tried first, falling back to a normal network-permitted load
on any failure, so a genuinely fresh, never-cached install can still
download the model once. Two consecutive full-suite runs after the fix:
506 passed, 2 xfailed, 0 failed, identical both times.

**Noted, not fixed (accepted tradeoff already documented for the
audiobook registry, now also applying here):** a process restart or a job
still running at interpreter shutdown can leave an orphaned upload file
with no matching row (if teardown/cleanup ran first) — `jobs.py`'s own
docstring already accepts this class of gap for exactly this reason ("an
empty registry after a restart is the truthful answer"). Encountered twice
during this stretch's test runs (both from test-process teardown races, not
production behaviour); cleaned by hand, not automated — matches the
existing accepted scope.

### Private search wired into the Librarian (`services/librarian.py`)

New tool `search_my_library`: query-only parameters (no `id`, no `user`,
nothing "user"-shaped — `test_no_tool_accepts_a_user_id` already asserts
this structurally across every tool including this one). Identity is
`ctx.account_id`, from the authenticated request via `Librarian.answer()`'s
own `account_id` parameter, exactly the same rule `check_user_library` is
already built on and stated in this module's own docstring: "No tool takes
a user id... The tools are unable to ask the question." Verified, not just
asserted: `test_a_user_id_smuggled_into_private_search_arguments_is_ignored`
passes a bogus `user_id`/`account_id` in the tool call's own arguments and
confirms it is never read; sabotage-checked by making the handler
actually honour an `args["account_id"]` override — caught immediately.

`search_private` (the new `LibrarianDeps` field, defaulted to `lambda ...: []`
so every existing construction — real or a test's fake — keeps working
unchanged) filters `search_chunks(user_id=account_id, ...)`'s results down
to `visibility == "private"`, rather than adding a second retrieval
function: `search_chunks` already returns exactly the right rows (public
catalogue plus this account's own private ones, per
`services.search.visible_chunks`), and `search_catalog` already covers the
public half — mixing them in one tool would blur the distinction the
system prompt asks the model to keep between the shared catalogue and the
reader's own uploads.

`ctx.seen` (the grounding record) now holds catalogue books under their
plain integer id (unchanged) and uploads under a `f"upload:{book_id}"`
string key — a deliberate namespacing, not cosmetic: catalogue ids come
from `main.BOOK_BY_ID`'s space (1..29,975 today) and upload ids from
`books.id`'s own sequence (in the high 100,000s today, purely as an
artefact of how many rows this database's sequence has issued across
restores). Nothing structurally guarantees those ranges never overlap, and
a collision would silently let one book's grounding record overwrite the
other's.

**Verified**: `test_librarian.py` gained 5 tests (grounding through the fake
dependency, the not-logged-in honest-empty-state, the smuggled-identity
sabotage above, the required-query check, and both tools' results
coexisting correctly in one turn without conflation) — all 39 tests in the
file pass, including the two pre-existing structural guards
(`test_no_tool_accepts_a_user_id`, the analogous smuggling test for
`check_user_library`) with the new tool in place. `test_library_ingest.py`
gained a direct test of `default_deps()`'s *real* `search_private` closure
(not the fake) against a real ingested upload, closing the one seam the
fake tool-loop tests deliberately do not cover.

---

## Deferred technical debt

Carried deliberately, with the reason. Each has a closing phase.

| ID | Item | Why deferred | Closes in |
|---|---|---|---|
| ~~F-07~~ | ~~No authentication anywhere~~ | **Closed** — writes in PR 2, reads in PR 3 | ✅ |
| ~~F-19~~ | ~~Synchronous TTS blocks the request~~ | **Closed** — 202 + poll, bounded in-process registry | ✅ |
| F-18 | Server-side desktop notifications (`plyer`) | Needs a real delivery channel + queue | Phase 6 |
| ~~F-13~~ | ~~LTR trained on constant features, circular target~~ | **Closed** — skew fixed (F-26), target replaced by reading depth (2026-09-11) | ✅ |
| ~~F-26~~ | ~~7/10 LTR features zero importance~~ | **Closed** — skew fixed; target decided and shipped as reading depth (2026-09-11) | ✅ |
| **F-14** | **"CF" was not collaborative filtering — renamed 2026-09-20, real CF deferred.** Measured before deciding: **0 registered users, 8 interaction events ever inserted** (4 remain, none carrying a `user_id`), 0 `reading_progress`, 0 `comments`, 0 `reminders`. The moat (F-42) has never collected anything, because nobody uses the app — it is local-only and OI-5 blocks deployment. CF is a *user*-item factorisation; with zero users it is undefined, not merely hard. The code was also worse than F-14 said: `ml/collaborative.py` factorised a `genre x book` matrix of `average_rating * log1p(ratings_count)` — no user dimension anywhere — so F-26 feature-importance tables read `cf_s` as evidence about collaborative filtering when it was evidence about genre popularity, at 0.24 of the blend. Renamed to `GenrePopularityFactors` / `genre_pop_s` / `WEIGHTS["genre_pop"]`, weight and formula untouched; golden baselines verified byte-identical before and after | **Unblock condition, stated so it is checkable: enough distinct accounts with overlapping book engagement to form a user-item matrix.** That needs real usage, which needs deployment (OI-5). Building CF against synthetic interactions first would repeat F-13/F-26 exactly: a model that looks trained and has learned nothing. **Blocker restated 2026-09-21: OI-5 closed, so this is no longer blocked on defects — it is blocked on the app being reachable by people, which is OI-3.** Left as-is would have been the stale-status pattern this file keeps catching | **Blocked on OI-3** |
| ~~F-17~~ | ~~Book "pages" return placeholder strings~~ | **Closed** — serves real Gutenberg text, honest empty state otherwise | ✅ |
| **F-26a** | **Reading depth is measured against the excerpt, not the book.** `book_texts` holds ~11 opening pages (20,358 chars avg), so depth means "did the opening hold the reader", not "how much of the book was read". Normalised by excerpt length because against a 300-page book every reader would score ≤ ~4% | Needs whole-book text. When it lands: switch the denominator in `services/reading_depth.py` to the full page count, re-baseline, and expect depth scores to fall. **Re-verified 2026-10-01 and still exactly true**: 6,305 `book_texts` rows, 20,358 chars average, `is_complete = true` on zero of them | **Phase 5 — now in progress, so this is due rather than future** |
| ~~F-11~~ | ~~Unmounted second frontend~~ | **Closed** — OI-1 resolved and `backend/static/` deleted 2026-09-08; the row survived the deletion by three weeks, which is the same stale-row pattern as F-52/F-53/F-60 below | ✅ |
| **F-20** | **The recommender stack refits on every boot.** `lifespan.fit_ml()` runs unconditionally at startup and `recommendation.fit()` rebuilds clustering, nearest-neighbour similarity, genre-popularity, the LTR ranker, comment SVD and the chat classifier each time. None of those are persisted | **Open, not blocked** — the only thing unblocking it needed was someone to look. Half-right as originally written: the LSA vector space *does* persist (`ml/embeddings.py` joblib-dumps `lsa_{dim}.joblib`), which is why boots are survivable, but that is the search space, not the recommender. **"Closes in: Phase 1" was impossible** — Phase 1 merged 2026-08-19. Cost is boot latency, not correctness; the care needed is that a persisted ranker must be invalidated when the catalogue or the target changes, or it silently serves a model fitted to data that no longer exists | **Unscheduled — ready to pick up** |
| **F-51** | **Ranking is not reproducible across separate model fits, for near-tied candidates.** Found while reviewing F-47's golden diff: two candidates ~0.001-0.005 apart in score returned in different order across separate fresh fits of the identical code and data, while agreeing every time within one fit. Rules out the bandit (unseeded, would vary within a fit too) and `MiniBatchKMeans` (`random_state=42` already set). Suspected cause: GPU floating-point non-determinism in the MiniLM embedding pass — the one input to ranking that is neither seeded nor cached across fits. Currently worked around in the golden tests with an evidence-based safe prefix (`MAX_SAFE_PREFIX` in `test_golden_ranking.py`), not fixed at the source | **Source isolated 2026-09-23, during F-62's investigation - that half is done, do not redo it.** Measured directly: MiniLM output is bit-identical within one fit and differs by ~1e-7 per component across separate fits, and the cause is batch composition, not the GPU being nondeterministic per se. What remains is only the *remedy* decision - a deterministic CUDA mode (slower fits, needs measuring) versus a documented, accepted tolerance - plus whatever golden baselines that moves. **Blocked on the product owner, 2026-09-26:** choosing and implementing it means repeated full embedding fits on the GPU, which is close enough to F-41's thermal incident to not start unasked. Touches the core ML fit; deserves its own review | Phase 8 (evaluation) |
| ~~F-53~~ | ~~The standing enrichment job ran green for three days while doing nothing.~~ | **Closed** — `/health` reports `enrichment.{pending, last_progress, days_since_progress, stalled}` derived from `max(books.enriched_at)`, ground truth rather than a run counter (2026-09-22). Its residual — the run history itself readable from the app, not just `logs/enrichment/history.log` — closed separately as `GET /api/health/enrichment-history` (2026-09-26); this row previously still listed that as outstanding after it shipped, corrected here | ✅ |
| **F-57** | **The nightly enrichment job would have sent readers' private book titles to Google.** Found while scoping Phase 4, before any upload feature existed. `scripts/enrich.py:pending_query` selects on `enrichment_status == 'pending'` and takes `sources: list[str] \| None = None`, applying **no source filter when that is None** — which is how the standing OI-7 job calls it. An uploaded book carrying the default `pending` status would have been queued and its title and author sent to the Google Books API: a third party learning what is in someone's private library, paid for out of the quota that is this project's binding constraint (F-30, OI-7). The same shape as the 'absent-as-negative' family — a default that was correct when every row was catalogue and silently widens as the data model grows | **Fixed**: `books.owner_id` (NULL = catalogue) plus `catalogue_only()` applied to all four passes that walk `books`. Written as `owner_id IS NULL`, not `source != 'upload'` — a denylist fails open, so the next private source anyone adds is included by default and invisibly | Closed 2026-09-22 |
| **F-58** | **`python -m ingest` would have deleted every uploaded book on the instance.** `ingest.prune()` removes every row whose `(source, external_id)` is absent from the catalogue JSONL — and an upload is never in that file. This is worse than F-57 because it destroys rather than leaks, and worse again because the README tells people to re-run ingest freely: *'`python -m ingest` is idempotent — re-run it any time to refresh metadata.'* That sentence would have been false the moment the first reader uploaded a book, and the data would have been gone with no error | **Fixed** by the same `catalogue_only()` filter. Also **demonstrated live, by accident** — see F-59 | Closed 2026-09-22 |
| **F-59** | **A new test called `prune()` and deleted the entire test database.** The first version of `test_reingesting_the_catalogue_does_not_delete_uploads` proved F-58 the obvious way: create an upload, run the real `prune()`, check it survived. It did survive. Everything else did not — all 29,975 books gone from `digikitab_test`, cascading to `book_vectors`, and the suite went from **454 passed to 11 failures** (golden ranking fell back to `tfidf_svd` because `load_content_vectors` is all-or-nothing). The cause was the test, not the code. **This is Phase A's fixture bug repeated almost exactly**, and the written lesson did not prevent it — which is the part worth recording. Found immediately only because the 0.66s runtime looked wrong for something that reads a 29,975-record file, and that prompted a check of the row counts | **Fixed** two ways. The test now asserts the claim in two halves — the upload is not in the set `prune` walks, and `prune` walks that set — so nothing commits deletions to whatever database happens to be configured. **The rule, stated for next time: a test may call a function that commits deletions only against data it created itself.** Restored by re-running `ingest` (29,975 books) and `book_vector_pass` (29,975 vectors, 100%) against `digikitab_test` | Closed 2026-09-22 |
| **F-61** | **The suite’s own test-cleanup fixture became a catalogue-wiping bug the moment Phase 4 added a foreign key.** `tests/conftest.py:clean_user_state` runs `TRUNCATE users, ... CASCADE` once per session to clear rows tests write. `TRUNCATE ... CASCADE` cascades at the level of *tables*, not rows: it empties every table holding any FK to the truncated one, whether or not a row actually references it. Adding `books.owner_id -> users.id` (this Phase 4 slice) silently enrolled `books` in that statement, and `books` cascades to `book_vectors`, `book_chunks`, `book_texts`. The fixture’s own docstring says "books are left alone" — it deleted all 29,975 of them at the start of every test session. The catalogue was restored twice before this was found, because each restore vanished on the very next suite run | **Fixed**: `users` is now cleared with `DELETE FROM users` (cascades per row, so `owner_id IS NULL` rows survive), not `TRUNCATE`. Two regression tests added in `test_upload_isolation.py`, including one that fails if `users` is ever added back to the `TRUNCATE` list. **General lesson for any future FK added to a small, frequently-truncated table:** `TRUNCATE ... CASCADE` is not scoped to the rows a fixture created — grep the schema for what points at the table before adding it to a bulk cleanup statement | Closed 2026-09-22 |
| ~~F-60~~ | ~~The test database was a migration behind, and nothing said so.~~ | **Closed 2026-09-26** — `tests/conftest.py` now compares the test database's `alembic_version` against the code's head at `pytest_configure` and fails loudly with the fix command, instead of surfacing as a confusing missing-column error. Row still read "Logged, not built / Needs building" for five days after it shipped | ✅ |
| **F-56** | **Bringing the stack up from this directory created an empty database beside the real one.** Compose names a volume `<project>_<key>` and the project defaults to the *directory name*. The containers that had been running all along belonged to project `final` (working dir `D:\final\final\final`, a stale copy of this repo) and therefore to `final_postgres_data`. `docker compose up -d` from `final_clean/` created `final_clean_postgres_data`: a brand new, empty Postgres, with the real one sitting untouched beside it. **Nothing was lost, because it was checked** — `select count(*) from books` answered `relation "books" does not exist`. Had it not been, the app would have booted against an empty catalogue, which is F-03's exact shape and does not fail loudly; 992 books of that day's enrichment would have looked gone | **Fixed**: both volumes are pinned by explicit `name:` in `docker-compose.yml`, so the data no longer depends on which folder Compose was invoked from. The `final_` prefix is kept deliberately — that is where the data actually is, and renaming would be a volume migration rather than a config change. **Third member of a family now worth naming: `digikitab` vs `digikitab_test`, the stale `D:\final\final\final` checkout, and now the volume. In each case the thing being edited was not the thing that was running.** | Closed 2026-09-21 |
| **F-55** | **The rate limiter is not deployment-ready for the deployment it is for.** `core/ratelimit.client_ip()` reads `request.client.host` and deliberately ignores `X-Forwarded-For`, which is correct with nothing in front. But uvicorn is started without `--proxy-headers` / `--forwarded-allow-ips`, so **behind any reverse proxy — which is how this would actually be deployed — every request carries the proxy's address**. Both limiters then collapse into one shared bucket: the first ten chat requests from anyone lock out everybody, and the limiter reads as protection while delivering a denial of service. Found while scoping OI-5; it applies to the audiobook limit that has been shipped since August, not just the new chat one | Not a code change so much as a deployment decision. **Decided 2026-09-21: leave it exactly as it is — no `--proxy-headers`, no proxy trust — and block it on OI-3.** The correct setting is a function of the deployment target, which is undecided: behind a proxy it needs `--proxy-headers --forwarded-allow-ips=<the proxy's IP>` *and* a `client_ip()` that honours the header it is then safe to trust; with no proxy the current code is already right. Picking one now means finding out later whether it was the right one, which is how a limiter ends up reading as protection while delivering a denial of service. Nothing is exposed while this waits — the app is local-only, which is what OI-3 is about | **Blocked on OI-3** |
| **F-54** | **`cf_sim` and `content_sim` have always been `0.0` for every book.** Found while renaming F-14. Both are read in `_to_api` as `round(_safe_float(b.get("cf_sim")), 3)`, and **nothing anywhere assigns them** — so `_safe_float(None)` returns 0.0 and the API reports two similarity scores that are structurally constant. Same family as the `taste_vector_dim: 0` claim removed from `/api/feedback` (Waiting-on-you #3): a field describing a measurement that never happens. No consumer found — neither name appears in the frontend | **Decided 2026-09-22: removed, not populated.** Swept for consumers first — no frontend page read either field, no test asserted on one, nothing else in the backend referenced them, and this repo has no git remote, so there was no contract to break. The honest values (`content_s`, `genre_pop_s`) are both in scope at scoring time, but nothing asked for per-component scores and `ml_score` already exposes the blend — filling them would have been building a feature to justify a bug. Also removed from `_gutenberg_to_api`, where they were `None` rather than `0.0`: **the two payload shapes did not even agree with each other**, which is its own small argument that nothing was reading them | Closed 2026-09-22 |
| ~~F-52~~ | ~~`test_every_priced_result_actually_has_a_known_price` fails intermittently — a `KeyError: 'availability'`.~~ | **Closed 2026-09-26** — root-caused by deterministic reproduction rather than re-running the flake: `_gutenberg_to_api()` omitted the key entirely, because `price_and_availability()`'s `infer_source()` misclassified a bare Gutenberg dict (no `book_id`, no `thumbnail`) as `google_books`. Fixed at the source and covered by two new tests | ✅ |
| **F-65** | **The standing enrichment job has made zero progress since 2026-09-25, and the reason is that it starts before the network does.** Every run since logs `processed 0, throttled True, quota_exhausted False`, coverage frozen at 29.07%, 20,281 still pending. Diagnosed 2026-10-01 by elimination, each step measured, none assumed: the API key is **valid** (one keyed request returned a clean JSON `400 Required parameter: q`, and the exact volume lookup the job issues first returned `200` with real JSON); the **quota is untouched** (a keyless request gets the shared-anonymous `429`, the keyed one does not); **DNS is clean** (`www.googleapis.com` → real `172.217.x.x`, no hosts entry); **no proxy** (`ProxyEnable=0`, nothing listening on the `127.0.0.1:12334` port from the Ollama bug, `urllib.request.getproxies()` empty); **not the legacy host** (both `www.googleapis.com/books/v1` and `books.googleapis.com` answer JSON); **not the User-Agent** (identical responses with and without the job's bot-style UA); **not Docker cold-start** (runs with Docker already up fail identically); **not the client stack** (the job's own `fetch_json` code path succeeds on a settled network). What is left is **when** it runs: the task is scheduled for 04:00 with `WakeToRun=False` and `StartWhenAvailable=True`, so it fires the instant the machine wakes — which is why observed start times cluster at 10:30–12:45 instead of 04:00. `RunOnlyIfNetworkAvailable=True` only checks that an adapter has a route, not that the internet is reachable, so the first requests go out over a just-associated connection. The symptoms match that exactly: an SSL handshake timeout on one run, and on the rest an HTTP 403 whose body is **HTML, not the Books API's JSON** — something other than Google answering | **Why it stays stuck rather than self-healing, which is the actionable half:** `fetch_json` raises on 403 with **zero retries** (403 is deliberately excluded from `RETRY_STATUS`), `ProviderUnreachable` subclasses `ProviderThrottled`, and `THROTTLE_LIMIT = 3` aborts the entire run. So three instant failures end the day's pass in **6 seconds** (10-01: start 12:44:51, done 12:44:57) and nothing retries for ~24h. A transient network state costs a full day of quota. **Diagnosis only — not fixed, per instruction.** Two candidate remedies, both cheap: gate the pass on a real reachability check before the first book, and/or stop treating a bare 403 as instantly fatal (retry it with backoff like 429, or require consecutive throttles to be spread over time rather than milliseconds). **Also worth fixing regardless of cause:** `fetch_json` truncates the 403 body to 120 chars and logs no response headers, so the only evidence left behind is a generic HTML `<head>` — the `<title>`, the body text past the cut, and `Via`/`X-Debug-Tracking-Id` would have identified the rejecter on day one and made this diagnosis a two-minute job instead of a long one | **Needs the remedy decision** |
| ~~F-66~~ | ~~**The audiobook player has never called the audiobook API.** `frontend/Audiobook.html:453` fetches `/audio/{id}`, which is not a registered route.~~ | **Closed 2026-10-02** — three faults, each hiding the next, and the reported URL was only the third. (1) `audiobook` was `pages > 350`: not a statement about audio at all, flagging **7,150** books of which **236** were synthesisable while **6,071** that were went unflagged — `_gutenberg_to_api` hardcoded `False` for the one source that works, so the flag was nearer inverted than imprecise. (2) The flag reached the player via `dataset.hasAudio`, and `dataset` values are strings: `"false"` is truthy, so *every* book took the real-audio branch regardless. (3) That branch fetched the unregistered URL and 404'd into a `catch` that started browser speech, so the failure was inaudible. Fixed: `audiobook_available()` in `services/catalogue.py` now answers from `source == "gutenberg"` (what `AudiobookEngine.generate` can actually fetch text for) and is shared by all three serialisers; the player calls `/api/audiobook/{id}/stream`, probes with HEAD to tell "not generated yet" from a real failure, and the F-19 generate/poll client that was never written now exists behind an explicit button. `format`'s `"Audiobook" if pages > 350` is the same falsehood in a third guise but has **no consumer anywhere** — left in place and noted rather than removed in the same change | ✅ |

---

## Credentials

| Key | Status | Notes |
|---|---|---|
| `GOOGLE_BOOKS_API_KEY` | **In place and verified working**, 2026-09-16 | The 2026-08-19 key was never found at the `D:\final\.env` path this table originally pointed to — neither there nor at the project root when checked on 2026-09-16 (OI-7 setup). Rather than guess whether it was lost, moved, or expired, the product owner issued a fresh key. Placed at `D:\final\final\final_clean\.env` (the path `core/config.py`'s `load_dotenv` actually reads) and confirmed live: a direct volume lookup, a plain search, and the exact `intitle:`/`inauthor:` query `enrich.py` uses all succeeded. Now driving the OI-7 standing job. |
| LLM provider | **Not needed** — local, decided 2026-09-18 | `qwen2.5:7b` primary, `qwen2.5:3b` fallback, via the Ollama already installed on this machine (F-22 Phase B). No API key, no cost. An API-based provider would slot in behind the same `LLMProvider` interface without touching callers, if that ever changes. |
| Embedding provider | Not requested yet | Phase 2 |
| TTS provider | Not requested yet | Phase 6 |
| Object storage | Not requested yet | Phase 6 |

---

## Phase 4, autonomous stretch (slices 2b-5) — started 2026-09-25

Product owner authorized proceeding through TXT+EPUB extraction, the
background job, and private-Librarian search wiring in one continuous pass,
without per-slice check-ins. Stop conditions: irreversible/destructive
actions with no safe path, a real product decision, or GPU-thermal risk.
Everything else — technical/sequencing/scope calls within this plan — is
mine to make and log here, not to ask about.

**Pre-flight check (as instructed): Docker containers had exited** (both
`digikitab-postgres`/`-redis` down; the 21:23 scheduled enrichment run
failed with a Windows pipe error, `-2147023829` — the same OI-8 sleep/wake
shape seen before, not new). Restarted from the correct directory
(`docker compose up -d` in `final_clean/`, reattached to the pinned
`final_*` volumes per F-56, no new volume created). Data confirmed intact
(29,975 books, 145,887 chunks) before proceeding.

**Enrichment: not stalled.** `last_progress` 2026-09-25 12:48 UTC, 0.22
days ago, well under `STALL_DAYS=3`. The log shows real progress since the
last check (26.57% -> 29.07% description coverage, 993 processed,
`quota_exhausted=True` — a clean stop, not a failure) before the container
outage interrupted the following scheduled run. No action needed beyond
the restart; the 4am run should succeed now that Postgres is reachable.

---

## Continuing autonomously — 2026-09-26

Product owner: keep working through unblocked, high-value items without
per-item sign-off or reports, same stop conditions as the Phase 4 stretch
(irreversible/destructive with no safe path; a real product/scope decision;
GPU-thermal risk). Stepping away for an extended period; one consolidated
report on return. Picked, with reasoning given up front: F-60, then F-52,
then F-53's residual enhancement, then whatever else is unblocked and
valuable. Explicitly not touching: OI-3 (deployment, on hold), OI-12
(Reading DNA, no spec), F-14/F-55 (blocked on OI-3), F-51 (Phase 8, treated
as GPU-thermal-adjacent — sustained embedding-fit comparisons risk the same
pattern F-41 already burned this machine on once).

## F-60 · Test DB migration-drift guard — **CLOSED 2026-09-26**

Built exactly as specified in the table entry: a check, run once via
`pytest_configure` (before collection, extending the existing
`REQUIRE_DATABASE` check there rather than adding a second hook), comparing
`digikitab_test`'s actual `alembic_version` against the code's own migration
head — read via `alembic.script.ScriptDirectory`, a filesystem operation,
not a second `alembic upgrade head` subprocess. A mismatch raises
`pytest.UsageError` with the specific drifted-vs-expected revisions and the
exact fix command, before any test runs — one clear message instead of the
eight confusing `column "owner_id" of relation "books" does not exist`
errors this exact gap has already produced once.

**Deliberately does not auto-migrate.** `_ensure_test_database` already
does, but only when the database does not exist yet. Once it exists and is
merely stale, silently upgrading it would also silently paper over the
other way this class of bug happens: `DIGIKITAB_TEST_DB` pointed at the
wrong database entirely. A loud failure is the point.

**Verified, including sabotage of both the guard and its own test:**
- Real sabotage against the actual database: `alembic downgrade -1` against
  `digikitab_test`, then ran a real test file — caught immediately, one
  line, naming both revisions and the fix command. Restored
  (`alembic upgrade head`) and confirmed back at `a73fc73a4646`, 29,975
  books intact, before doing anything else.
- `tests/test_conftest_guards.py` (3 tests): the steady state (no
  exception when at head), the mismatch (mocking the code's own reported
  head via `ScriptDirectory.get_current_head`, not the real database's
  `alembic_version` row — proves the comparison without any risk of
  leaving the shared test database mismatched if the test itself failed
  partway through), and that an unreachable database is a silent no-op
  (a different, already-handled degradation path) rather than a second,
  unrelated failure on top of it.
- Sabotage-verified the test file too: commented out the guard's `raise`,
  confirmed `test_fails_loudly_on_a_mismatch` fails as it should, reverted,
  confirmed clean.

Docker CLI (`docker ps`, `docker exec`) was persistently slow/unresponsive
(120s+ timeouts) throughout this item — noted to the product owner
separately, not investigated further since every actual database
operation (via direct `psycopg`/SQLAlchemy connections, including the
sabotage above) succeeded normally throughout, indicating the CLI/API
layer rather than the containers or Postgres itself.

---

## F-52 · Intermittent `KeyError: 'availability'` on `/api/recommend` — **CLOSED 2026-09-26**

Root-caused rather than assumed, per the table's own note that it "needs
reproducing deliberately." `POST /api/recommend` queries
`GutenbergClient.search()` — a live call to `https://gutendex.com`,
unconditionally, on every request — and fuses whatever it returns into the
ranked results (`fuse_and_rank`). `_gutenberg_to_api`, which shapes those
results, built a dict with **no `"availability"` key at all** — not even
`"unknown"` — so any caller reading `book["availability"]` directly (not
`.get(...)`) crashed the instant a Gutenberg-sourced item ranked into the
top N. That ranking depends on a live external search whose results vary
run to run, which is exactly "genuinely intermittent."

**Confirmed deterministically** rather than by re-triggering the live
search enough times to get unlucky (gutendex.com was itself timing out
during this investigation — a separate, unrelated network blip):
`_gutenberg_to_api` is a pure function of the fixed dict shape
`GutenbergClient.search` returns, so the bug reproduces without the
network's cooperation at all.

**Fix is not "route it through `price_and_availability`"**, the function
`_ml_to_api` already uses for exactly this reason (shared logic, so the two
serialisers cannot disagree) — checked and rejected: that function infers
source from `book_id`/`thumbnail` via `infer_source`, and a Gutenberg search
result carries neither. `infer_source(None, None)` falls through to
`"google_books"`, the wrong answer, not the honest one — reusing it naively
would have swapped one bug for a worse, silent one. `_gutenberg_to_api`'s
input source is already unambiguous (the function exists only for Gutenberg
results), so `price_and_availability`'s own special-cased constant for that
source (`0.0, "free_public_domain"`) is hardcoded directly instead, with a
comment explaining why the shared function was not reused here.

**Verified**: 2 new tests in `tests/test_availability.py` — the dict shape
directly, and the full `fuse_and_rank` path a real caller goes through.
Sabotage-verified: removed the fix, both tests failed with the exact
original `KeyError: 'availability'`; reverted, confirmed clean.

---

## F-53's residual enhancement · enrichment run history readable from the app — **CLOSED 2026-09-26**

`GET /api/health/enrichment-history` — the run-by-run outcome the standing
OI-7 job has actually logged, readable without a terminal on whichever
machine is running it. Deliberately **not** a second source of truth about
whether enrichment is working: `_enrichment_health`'s `max(enriched_at)`
stays that, because F-53 is exactly the case where the log said a run
succeeded and nothing had actually happened. This answers a different,
narrower question — what did the job report — useful once
`_enrichment_health` already says something is wrong and a human wants the
last several runs without SSH/RDP to the machine.

Parses `logs/enrichment/history.log` (written entirely by
`run_daily_enrichment.ps1`; this backend has never read it before now) into
one dict per run, reading whatever keys are actually present rather than
assuming a fixed schema — a barren run logs fewer keys than one with real
progress. Bounded to the last 30 runs returned, though `total_runs_logged`
still reports the true count, so a long-running instance's endpoint answers
in constant time without hiding how much history exists.

**Verified**: 7 new tests in `tests/test_enrichment_health.py` (18 total in
the file) — the parser against a synthetic multi-run log, against the real
log file (existence/shape only, not content, which changes daily), the
missing-file honest-empty-state, and the bounding behaviour. Sabotage
-verified twice: the bounding logic (caught immediately) and the
malformed-line handling — where the first attempt at that test was itself
too weak (garbage placed only *before* the first run header is trivially
ignored by an unrelated guard regardless of whether the parser's own regex
is strict, so a bare run-count assertion passed even against a
deliberately-broken, anything-matches regex). Strengthened to place garbage
*after* a valid header and assert the run's own keys are unchanged, which
does catch it — the fix was to the test, not the code, since the code was
already correct.

---

## README audit — found checking F-24, turned into a full pass — 2026-09-26

Started as a five-minute status-correction for F-24. Checking whether the
README already had the venv note it recommended surfaced that the whole
file was drifted, badly, in the direction that matters most: telling a
reader the project is *less* capable and *less* safe than it actually is.

**Confirmed stale, one by one, against the actual current code — not
assumed from the finding's age:**

- **Node.js/`npm install`** — listed as a requirement. `package.json` and
  `server.js` do not exist in this repository; confirmed with `find`, not
  inferred from OI-5's own text. Express was retired 2026-09-21.
- **`.\scriptsun.ps1`** — the documented launch command. A literal stray
  carriage-return byte sat where a backslash should be (`.\scripts` + `\r`
  + `un.ps1`), so copy-pasting it would have failed outright. Took several
  attempts to fix cleanly — `sed`/`perl` pattern-matching around the
  embedded control character kept silently failing in ways that looked
  like they had worked, and a blind `sed -i '<line>s/.../.../'` by line
  number landed on the wrong line entirely after the file had already
  grown from earlier edits in the same pass (exactly the "look at the
  target before overwriting" lesson, briefly not followed and caught
  immediately after). Resolved by reading the file's actual current
  content in full and rewriting it with an exact-string tool rather than
  continuing to guess at shell-escaping.
- **`GET /api/profile/{user_id}` "not yet protected"** — confirmed
  protected: `api/profile.py` takes `CurrentUser`, not `OptionalUser`.
- **"Audiobook generation is synchronous"** (F-19) — confirmed async:
  `api/audio.py` submits to `jobs.REGISTRY`, unchanged since F-19 closed.
- **"All state is in-process memory... restarting loses every comment"**
  (F-12) — confirmed false: `Comment`, `ReadingProgress`, `Reminder` are
  real, persisted SQLAlchemy models. The one part of this claim that is
  still true — single-worker only, because the job registries are
  in-process — survives as its own, correctly-scoped bullet.
- **"Book pages return placeholder text"** (F-17) — confirmed false:
  `api/books.py`'s pages route reports real `text_available` state and
  serves genuine Gutenberg text where it exists.
- **"Every catalogue description is empty"** (F-15) — confirmed false as
  stated (29.07% coverage today, not 0%), though genuinely still
  incomplete — rewritten to point at the live, honest source
  (`/api/health/enrichment-history`, `/health`) instead of a number that
  would itself go stale the next time enrichment ran.
- **"The chatbot never returns book recommendations"** (F-22) — confirmed
  false, most consequentially: `ChatbotEngine.respond()` calls
  `self.librarian.answer(...)` and returns real, grounded books from the
  full tool-calling loop this project has spent multiple phases building,
  including this same autonomous stretch's own Phase 4 slice 4. The README
  described a chatbot from before F-22 existed at all.

**What replaced it**: a "Known limitations" section rewritten from only
currently-true claims, each checked against the code rather than carried
forward from the last time someone wrote this section — plus the currently
-accurate deployment/collaborative-filtering/rate-limiter blockers (OI-3,
F-14, F-55), F-51's ranking non-determinism, Phase 4's ingest-only
copyright posture, and OI-12's Reading DNA gap, none of which the old
section mentioned at all. Also updated: the Layout section (missing
`api/library.py`, `services/librarian.py`, `services/extraction.py`,
`services/library_ingest.py`, `backend/uploads/`, and the stale
`ml/collaborative.py` name after F-14's rename), and the Tests section
(claimed "dependency-light... assert against source text," true of PR 1's
original ~30 tests, false of the 550+ that now mostly require the real
database and full ML stack).

Zero code changed — this is a documentation-only commit. Ran the full
suite once anyway (a habit, not the destructive-recovery two-run bar,
which does not apply here) to confirm nothing about the working tree state
was accidentally disturbed while investigating.

---

## Real bug found while running the "should be a no-op" README suite check — 2026-09-26

The full-suite run meant only to confirm the docs change disturbed nothing
came back with 3 failures, all in `test_llm_provider.py`, all against
tests whose own names say they simulate an *unreachable* Ollama —
suspicious on its own, since Ollama was confirmed running (`curl` → 200)
moments before. Chased rather than dismissed as flakiness, per the
standing method.

**Root cause, measured directly rather than guessed**:
`urllib.request`'s default opener honours whatever proxy Windows/WinINET
has configured system-wide, and — unlike curl and most browsers — does not
exempt loopback addresses from it. This machine has a system proxy at
`127.0.0.1:12334` (`urllib.request.getproxies()`), and every call through
the default opener to `127.0.0.1:11434` (Ollama) was being routed through
it and reset. Reproduced directly: a bare `urlopen` against the real,
live Ollama failed with the identical `ConnectionResetError [WinError
10054]` the "unreachable" tests were built to simulate — confirming this
is not a test artefact but a real path the actual `OllamaProvider.chat()`
goes through in production too, on any machine with any system proxy
active, for a reason entirely unrelated to Ollama's own health.

**Two fixes, not one**:

1. `_NO_PROXY_OPENER` (`services/providers/llm.py`) — a `urllib` opener
   built once with an empty `ProxyHandler`, bypassing system proxy
   discovery entirely. Correct unconditionally here: this module's own
   docstring states Ollama is local by design decision (2026-09-18), so
   there is never a legitimate reason to proxy a call to it. `chat()` now
   uses this opener instead of the module-level `urlopen`.
2. The exception handler that turns a failed call into `LLMUnavailable`
   caught `(URLError, TimeoutError, JSONDecodeError)` — which covers a
   failed *connection attempt* but not a connection that succeeded and was
   then reset while the response was being read, which raises a raw
   `ConnectionResetError` (an `OSError` subclass) that was reaching the
   caller as an unhandled stack trace. Widened to `OSError` (which already
   covers `TimeoutError`), verified not to swallow
   `json.JSONDecodeError` (not an `OSError` subclass) or anything raised
   by malformed response *content* outside the try block.

The test file's own `ollama_reachable()` had the identical bug — it also
used a bare `urlopen`, so it was reporting Ollama unreachable and silently
*skipping* four "really" tests against the real models, on a machine where
Ollama was genuinely healthy. Fixed the same way. The combined effect: sabotaging
just the production fix (reverting `_NO_PROXY_OPENER` back to plain
`urlopen`, leaving the test's own probe fixed) now produces four loud,
correctly-attributed failures instead of four silent skips — a strictly
better failure mode than before this was found, independent of whether the
proxy fix itself is ever reverted by accident.

**Verified**: `test_a_connection_reset_mid_response_is_also_llm_unavailable`
pins the exact `ConnectionResetError` regression deterministically (mocked,
not relying on this machine's specific proxy setup, which is not portable).
Sabotage-verified both fixes independently — reverting the exception-catch
widening reproduces the original unhandled-exception failure exactly;
reverting the no-proxy opener reproduces all four original failures with
the identical `WinError 10054` message. All 19 tests in
`test_llm_provider.py` pass with zero skips (previously 4) once both fixes
and the test's own probe are in place together.

---

## Phase 5 first slice · Reading Copilot (§32) — 2026-09-29

Phase 4 finished with every piece §32's pipeline needs except one, so this
slice is that piece plus the loop built on it. Section 32's own sequence:
question, **book-scoped retrieval**, passages, LLM, grounded answer,
citation.

### The missing primitive: book-scoped retrieval

`search_chunks` could filter by language, genre, year and embedding model
but not by book, so "explain chapter 4 of *this* book" would have been
answered from whichever passage in the 6,000-book corpus was the nearest
neighbour. Added `book_id`.

**The design point, and what its tests target:** it is AND-ed onto
`visible_chunks(user_id)`, never substituted for it. Scoping to a book the
reader asked about must not become a way to see more inside that book than
they may. Sabotage-verified by making it replace the visibility predicate:
two tests fail, and the anonymous one shows the real severity — an
unauthenticated caller receiving both readers' private chunks for that
book. Also tested: scoping to a book with no chunks returns empty rather
than silently falling back to the corpus, which is the failure that would
answer a question about one book with another book's text.

### The loop, and the part that is not taken on trust

`services/copilot.py` + `POST /api/books/{book_id}/ask`.

Section 32 requires the assistant to distinguish `Known from book` /
`Inference` / `Uncertain`. The easy reading is to ask the model for that
label and print it — which would make the most load-bearing field in the
response the one nothing verifies. This project has already answered that
question once, for F-22: the fix for "the model might name a book it never
saw" was not to ask it nicely. Two checks are cheap and real:

1. **No passages, no knowledge.** If retrieval returned nothing there is
   nothing in the book to have known, so the label cannot be `known`
   whatever the model claims — and the model is not called at all, because
   spending a generation to discover there is nothing to ground on is
   exactly how a confident answer about an unavailable book gets produced.
2. **A quoted span must be in a passage.** Words presented as the book's
   own have to appear in something retrieval actually returned, normalised
   the same way `librarian._grounding_problem` normalises titles. An
   unsupported quote downgrades the label to `uncertain` **and is
   reported** — `label_downgraded_from` and `unsupported_quotes` are in the
   response, so a caller can tell the label was not the model's own claim.

Deliberately bounded, with reasons rather than omissions:

- **The three-word floor on quote checking.** A two-word quotation is
  emphasis or a term of art; flagging it would make every answer that
  stresses a word look like an invention. Tested both ways.
- **Punctuation and whitespace are normalised before comparing.** A model
  reflowing a quote is not the failure this guard is for.
- **`MIN_PASSAGE_SIMILARITY` (0.02) is below `search.MIN_SIMILARITY`.**
  Catalogue-wide search is choosing between 6,000 books and can afford to
  be strict; here the book is already decided and the only question is
  which of *its* passages are relevant.
- **No memory between calls.** §33's per-(user, book) memory is its own
  feature with its own storage; a summarised-memory layer belongs on top of
  this loop, not smuggled into it.
- **No pages served.** Passages reach the reader as citations supporting an
  answer, which is retrieval — what OI-4's posture permits for an upload.
  Rendering the book back is what it does not.
- **`OptionalUser`, not `CurrentUser`.** `search_chunks` already decides
  what a caller may see, so an anonymous reader can ask about a
  public-domain book while a signed-in one additionally reaches their own
  uploads. `AskRequest` is `extra="forbid"` so a caller-supplied id is a
  422, not a silently-ignored field.
- **Rate-limited on `/api/chat`'s limits under its own scope key.** One
  question is one generation against the single local GPU; the two features
  get independent buckets at the same ceiling.
- **A model outage degrades rather than fails.** `LLMUnavailable` (all
  tiers down, or over capacity) returns the retrieved citations with an
  honest `uncertain`, because retrieval already succeeded and is useful on
  its own.

**Verified**: 20 tests. The quote checker directly (supported, invented,
reflowed, two-word, smart quotes); the label logic against a model that
lies, a model that omits the label, and a model that is absent; that the
passages actually reach the prompt (a prompt that forgot them would still
produce plausible answers — from the model's memory of the book, which is
what the whole pipeline exists to avoid); and the endpoint end to end
through the real app with only the model faked. Sabotage-verified both
checks independently: disabling the downgrade, and disabling the
no-passages guard — the latter caught by the real-database test too, and it
produces exactly the predicted failure, a `known_from_book` answer about a
book the system holds no text for.

---

## Phase 5 second slice · AI Book Memory (§33) — 2026-09-30

The natural next layer on `services/copilot.py`, whose own docstring
already said where this belonged: *"a summarised-memory layer belongs on
top of it rather than smuggled into it."* Section 33's requirement is
specific and directly testable: maintain memory per (reader, book), and
**do not replay raw transcripts forever** — conversation, periodic
summarisation, structured memory, in that order.

### Enforced at storage, not just in the prompt

`book_memory` (migration `a9011ba4228d`, applied and round-tripped on both
databases): one row per turn — `user_id`, `book_id`, `summary`,
`created_at` — append-only, never overwritten. `services/memory.py`'s
`record_turn` never receives or stores the raw question or the model's
full answer, only a short summary bounded to `MAX_SUMMARY_CHARS` (240) —
a caller cannot regress this into transcript storage by passing something
longer; it is truncated at a word boundary. One row per turn rather than
one mutable row per (user, book) deliberately: collapsing many turns into
one entry is a real compaction problem, and reading the most recent N rows
already gives continuity without ever needing to solve it for this slice.

**Summarisation degrades, it does not fail the turn.** No model available,
or the model raises: `summarize_turn` falls back to a plain, deterministic
"Asked: {question}" line rather than losing the memory entry or crashing a
turn whose actual answer (the valuable part) already succeeded — the same
section-12 shape the rest of this project follows.

**Not grounded against the book, and does not need to be.** Unlike
`copilot.ask_about_book`'s answer, a summary compresses a conversation that
already happened (a question, and an answer that was itself grounded when
it was produced) rather than making a new claim about the book's content.
The property that matters here is brevity, not faithfulness-to-the-text,
which is why this module carries no quote-checking of its own — that
already lives in `copilot.py`, and duplicating it here would be checking
the same thing twice for the wrong reason.

### Wired at the API layer only

`api/copilot.py`: reads this reader's recent memory before asking (passed
into `ask_about_book`'s new, backward-compatible `prior_context` parameter
— `None` by default, every one of the 20 pre-existing copilot tests
unaffected), writes a new summary after a real answer exists. `services/copilot.py`
still does not import or know about `services/memory.py`, matching its own
stated design. An anonymous caller reads and writes nothing —
`services.memory` already no-ops for `user_id=None`; the endpoint just
calls it the same way for every caller rather than branching on identity
itself.

**The GPU-concurrency detail worth stating:** summarisation is a second,
separate `llm_module.slot()` acquisition after the main answer's slot is
released, not held open longer inside it. If that second slot is saturated,
`record_turn` still runs with `llm=None`, so a busy GPU degrades the
memory entry's quality rather than losing the turn's memory entirely.

**Verified**: 15 tests in `tests/test_memory.py` (the property that
matters most — a raw question/answer never survives verbatim in the
summary — plus truncation, word-boundary cutting, model-outage and
model-exception fallbacks, and the storage path: round-trip, per-book
scoping, per-reader scoping, the bounded-and-most-recent-first read, and
append-only writes proven by asserting two rows exist rather than one).
2 new tests in `tests/test_copilot.py` proving the wiring end to end
through the real app: a second question's own prompt to the model
contains a short derived trace of the first turn and *not* the full first
answer, and an anonymous caller leaves no row behind at all.

Sabotage-verified three times: dropping the `user_id` filter from
`recent_memory` (caught immediately — cross-reader memory leakage);
allowing an anonymous `record_turn` through (caught by the test, and
independently refused by `book_memory.user_id`'s own `NOT NULL`
constraint — the database itself has no representation for a memory entry
with no owner); and disabling the endpoint's own read of prior memory
(caught by the end-to-end test, with the actual failed prompt shown in the
assertion — the model was correctly still answering the second question
from the retrieved passages, just with no memory of the first).

Two consecutive full-suite runs: 560 passed, 2 xfailed, 0 failed,
identical both times.

Found along the way, unrelated to this slice: Docker containers had again
exited on their own mid-session (the same OI-8 sleep/wake pattern as
before) partway through building this migration. Restarted from the
correct directory, data (29,975 books, 145,887 chunks) confirmed intact
before the migration was applied.

## Phase 5 third slice · book summaries (§38) — 2026-09-30

§37 (Notes as Knowledge) and the rest of §38 (flashcards, quizzes, spaced
repetition, progress tracking) were assessed and deliberately **not**
built this pass. Each needs a real product decision this project has no
standing to make alone — a note's data model, how a "concept" gets
identified for §37's cross-book graph, which spaced-repetition algorithm,
what a flashcard even is here — the same shape that kept Reading DNA
(OI-12) and Explainable Recommendations (§26) unbuilt. §38's first listed
feature, "summaries," was the one exception: concretely specified, needing
no invented data model, and §38 itself gives the constraint that makes it
buildable at all — *"Reuse the same RAG infrastructure. Do not create a
second independent AI knowledge system."*

### Why this needed a new retrieval primitive, not just a canned question

The obvious shortcut — call `ask_about_book(question="Summarize this
book")` — was rejected. `search_chunks` ranks passages by similarity to a
query, and "summarize this book" is itself a query: it would retrieve
whichever dozen passages happen to sit nearest that phrase in vector
space, which for a novel-length book is a handful of passages from
wherever the text happens to use summary-adjacent language, not a
cross-section of the book. A summary needs breadth, not relevance.

`services/search.py` gains `representative_chunks(session, book_id,
user_id=None, limit=16)`: orders a book's own chunks by `ordinal` (its
reading order, not a vector space) and takes evenly spaced positions
across the full range, so a 20-chapter book is sampled from its beginning,
middle, and end rather than its opening pages. Same `visible_chunks
(user_id)` predicate `search_chunks` uses — a caller cannot see more of a
book's own passages here than semantic search would already show them.
Deliberately does **not** filter by `embedding_model` or require an
embedding at all: there is no vector comparison to keep consistent, only
each chunk's own stored text, so a book can be summarised even on an
instance with no encoder configured — a strictly wider availability than
`/ask`, worth noting since it means `GET /api/books/{id}/summary` never
needs the 503 `/ask` returns when semantic search isn't set up.

### The same grounding discipline, honestly adapted

`services/copilot.py` gains `summarize_book`, built on
`representative_chunks` instead of `search_chunks`, reusing
`unsupported_quotes` unchanged: a quoted span in the summary still has to
appear in a sampled passage, or it did not come from this book's text. No
`known_from_book`/`inference`/`uncertain` label, on purpose — a summary is
not one checkable claim the way an answer to a question is, so forcing
that label onto it would be asking a question the feature doesn't have.
The honesty a summary owes instead is coverage: the prompt requires the
model to state, as part of the summary, that it is built from sampled
excerpts and is not the complete text — sixteen passages spread across a
novel is a cross-section, and presenting it as a full synopsis would be
its own kind of fabrication.

No hits (a book with no visible text) short-circuits before the model is
called, same reasoning as `ask_about_book`'s check 1: nothing retrieved
means nothing to have summarised, and asking anyway is how a confident
summary of an unavailable book gets produced. No model available degrades
to returning the sampled passages alone, section 12's shape.

### Wired at `GET /api/books/{book_id}/summary`

Thin, matching `/ask`: `OptionalUser`, same `copilot` rate-limit bucket as
`/ask` (`ratelimit.CHAT_LIMIT`/`CHAT_WINDOW`) rather than a second budget —
both are one generation against the single local GPU, and a per-caller
ceiling should bound the two together. `GET`, not `POST`: unlike `/ask`
there is no request body, and summarising a book is a read of a derived
resource, not a submission.

**Verified**: 6 new tests in `tests/test_search.py` for
`representative_chunks` (spans the whole ordinal range rather than
returning a prefix, returns everything when the book is smaller than the
limit, works with no embedding present, empty for a book with no chunks,
and the same visibility guarantees `search_chunks(book_id=...)` already
has — a reader's own private chunk visible, another reader's never). 9 new
tests in `tests/test_copilot.py`: `summarize_book`'s no-model-call-on-empty-
retrieval, no-model-still-returns-passages degrade path, the sampled
passages actually reaching the prompt, citations bounded at
`SUMMARY_MAX_CITATIONS`, an unsupported quote reported, a fully-supported
summary reporting none, plus 3 endpoint tests through the real app
(citations present, reachable anonymously, a book with no text answered
honestly with the model never consulted).

Sabotage-verified three times: reverting `representative_chunks`'s spread
to a plain prefix (`rows[:limit]`) — caught immediately, the spread test's
own failure message names the fault line ("never reaches the back of the
book — this is a prefix, not a spread"); disabling the quote check in
`summarize_book` — caught by the unsupported-quote test; and disabling the
no-hits short-circuit — caught by the model-not-called assertion, with the
actual (unwanted) prompt shown in the failure.

Two consecutive full-suite runs: 575 passed, 2 xfailed, 0 failed,
identical both times.

## Phase 5 fourth slice · Reading Intelligence (§39) — 2026-09-30

Section 39's own examples were the test of buildability here, the same
standard applied to every slice this pass:

    "You normally read for 25 minutes in the evening. Continue Chapter 7?"
    "You haven't opened this book in six days. Want a three-minute recap?"

Both are answerable from data already flowing in — `interaction_events`
rows of type `READING_PAGE` already fire once per genuine page turn
(`api/books.py::_record_page_turn`, shipped for F-26) and are already
attributed to a signed-in reader (OI-6 follow-through). No new client
contract, no new table: this slice is a read of history that already
exists, not a new collection mechanism.

### What was deliberately left unclaimed

Two things the examples imply that this module does not say, both logged
rather than guessed past:

- **Time of day** ("in the evening"). `users` carries no timezone. A
  server-side UTC timestamp cannot honestly say *evening* for a reader in
  a different timezone — it would be a confident, specific, wrong claim
  about *when* someone reads, not an approximation of something true. The
  nudge below reports a typical **duration** instead, which needs no
  timezone to be correct.
- **Completion estimate**, section 39's own listed item. The only total
  page count on record is recovered by dividing `reading_progress.page` by
  the client-reported `progress` fraction — unreliable exactly where it
  matters most, early in a book, where a small denominator magnifies
  rounding into a wildly wrong total. Dividing one approximation by
  another and presenting the result as "time left" is the same failure
  §26 (Explainable Recommendations) was left unbuilt to avoid: a plausible
  number built on data that is not actually there. Left out; logged here
  rather than shipped as a confident-looking guess.

### The derivation (`services/reading_intelligence.py`)

`_cluster_sessions`: consecutive page-turn timestamps more than
`SESSION_GAP_MINUTES` (20) apart start a new sitting — an engineering
threshold in the same category as `search.MIN_SIMILARITY`, not a claim
about what the reader actually did between two turns. `_streak_days`:
consecutive UTC calendar days with at least one genuine turn, plus days
since the last one — same "only a real page turn counts" filter
`services/reading_depth.py::aggregate` already applies (`page_size == 1`
and `had_content`), reused rather than re-derived so the two features
cannot quietly disagree about what a real page turn is. `_nudge` produces
one of section 39's two shapes or nothing — an absent nudge is the honest
answer for a first session or too little history, and inactivity is
checked before typical-duration, on purpose: telling a reader who has not
opened a book in a week "you normally read for 25 minutes" would be a
strange thing to say when the recap offer is the truer one.

### Wired at `GET /api/books/{book_id}/reading-stats`

New router, `api/reading_intelligence.py`, not folded into `api/reading.py`
or `api/copilot.py`. Its own docstring states the reason plainly: `book_id`
here is the real `books.id` — the same space `/ask` and `/summary` already
use, and what `interaction_events`/`reading_progress` are foreign keys to —
deliberately **not** `api/reading.py`'s positional catalogue id (a
DataFrame row index + 1, translated internally via `store.resolve_book_pk`
before anything is stored). Folding this into either existing module would
have either mixed the two id spaces silently or misfiled a non-LLM feature
under the copilot router. `CurrentUser`, not `OptionalUser`: every number
here is one reader's own history, and an anonymous caller has none to
attribute back to them.

**Verified**: 24 new tests in `tests/test_reading_intelligence.py` —
session clustering (close turns merge, a long gap splits, the boundary
itself does not split, duration is start-to-end not a count), streak
arithmetic (a run of days, a skipped day breaking it, same-day re-reads
counting once, days-since-last-read), the nudge's priority rule and its
honest-absence case, then the real storage path: two sessions counted
correctly end to end, a bulk fetch (`page_size != 1`) and a contentless
page each correctly excluded, long inactivity reported honestly, another
reader's page turns never leaking into this reader's numbers, and the
endpoint requiring a signed-in caller.

Sabotage-verified three times: disabling the session-gap split (caught —
three turns 55 minutes apart collapsed into one session instead of two);
disabling the genuine-turn filter (caught by both the bulk-fetch and the
no-content test simultaneously); and disabling the inactivity nudge's
priority check (caught — a reader inactive for ten days was told about
their typical session length instead of offered a recap).

Two consecutive full-suite runs: 599 passed, 2 xfailed, 0 failed,
identical both times. No test debris left behind — checked directly
afterward: zero leftover test users, zero orphaned `reading_page` rows.

## F-65 · The enrichment stall — diagnosed by elimination, 2026-10-01

Six days of `processed 0`, coverage frozen at 29.07%, 20,281 books still
pending, every run exiting 0. Flagged by the product owner, not by any
alert — which is F-53's lesson repeating one level up: `/health` *does*
report `enrichment.stalled` correctly, and nobody was reading `/health`.

**Diagnosis only, as instructed. Nothing fixed, no quota spent on retries**
— three diagnostic requests total, two of them keyless, and the pass itself
was never re-run.

### What it is not

Each of these was measured, not reasoned about. Every one came back
negative, which is the useful part — the list of ruled-out causes is what
makes the remaining explanation credible rather than merely plausible.

| Hypothesis | Test | Result |
|---|---|---|
| Key revoked or restricted | one keyed request | `400 Required parameter: q` — **valid**, and accepted |
| Key's quota exhausted | keyed vs keyless | keyless gets the shared-anonymous `429`; **the keyed one does not** |
| Books API disabled on the project | discovery doc | served fine, `200 application/json` |
| DNS hijack / hosts override | `Resolve-DnsName`, hosts file | real Google `172.217.x.x`, no override |
| The `127.0.0.1:12334` proxy from the Ollama bug | registry, listening ports, `urllib.request.getproxies()` | `ProxyEnable=0`, nothing listening, urllib sees no proxy |
| Legacy host retired (`www.googleapis.com` → `books.googleapis.com`) | both hosts, same path | **both** answer with proper JSON |
| Bot-style `User-Agent` blocked at the edge | same request with and without it | byte-identical responses |
| Docker/WSL cold-start churning the network | correlate `docker_launched` against outcome across 14 runs | runs with Docker **already up** fail identically — hypothesis falsified |
| Python `urllib` TLS fingerprint vs PowerShell | the job's **own** `fetch_json`, same key, same UA, same URL | `200`, real JSON |

### What it is

The task is scheduled for **04:00** with `WakeToRun = False` and
`StartWhenAvailable = True`. The machine is asleep at 04:00 (OI-8), so the
run fires the moment it wakes — which is why the observed start times
cluster at **10:30–12:45**, never 04:00. `RunOnlyIfNetworkAvailable = True`
checks only that an adapter has a route, not that the internet is
reachable, so the first requests leave over a connection that has
associated but is not yet usable.

The symptoms match that and nothing else: one run failed with a raw
**SSL handshake timeout**, and the rest with an HTTP **403 whose body is
HTML, not the Books API's JSON** — something that is not Google answering.

### Why it stayed stuck for six days instead of self-healing

This is the part worth fixing regardless of what the network was doing.

`fetch_json` excludes 403 from `RETRY_STATUS` **by design** — the comment
says retrying a bad request just burns quota, which is right for a real
403. But it raises `ProviderUnreachable`, which subclasses
`ProviderThrottled`, and `enrich.py` counts those against
`THROTTLE_LIMIT = 3` and aborts the whole pass. So three instant failures
end the day's enrichment in **six seconds** — 10-01 started 12:44:51 and
was done 12:44:57 — and nothing retries for another ~24 hours. A few
seconds of unusable network costs a full day of quota, and the circuit
breaker's own tripwire is what converts a transient fault into a
six-day outage.

Two candidate remedies, both cheap, **neither implemented** (the remedy is
the product owner's call): gate the pass on a real reachability check
before the first book, and/or stop treating a bare 403 as instantly fatal
— either retry it with backoff like 429, or require the three consecutive
throttles to be spread over time rather than milliseconds.

### The instrumentation gap that made this expensive

`fetch_json` truncates the error body to 120 characters and logs no
response headers, so six days of logs preserved exactly one thing about
the 403: a generic HTML `<head>`. The `<title>`, the body text past the
cut, and the `Via` / `X-Debug-Tracking-Id` headers would have named the
rejecter on day one. Worth fixing on its own terms, independent of this
finding — the same argument F-53 made for reporting against ground truth:
an error that does not record what it was cannot be diagnosed later, only
re-encountered.

## F-66 · The audiobook player had never called the audiobook API

Reported by the product owner 2026-10-01 as one wrong URL. It was three
faults stacked, and each one concealed the one beneath it.

**Fault 1 — the flag was not about audio.** Both payload builders set
`audiobook` from page count:

| builder | rule | result |
|---|---|---|
| `services/catalogue.py:_row_to_book` | `pages > 350` | the `/books` payload the player reads |
| `services/recommendation.py:_ml_to_api` | `page_count > 350` | the same claim on the ML path |
| `services/recommendation.py:_gutenberg_to_api` | `False`, hardcoded | **the only source that can be synthesised** |

Measured against the database before changing it:

| | books |
|---|---|
| flagged as having an audiobook | 7,150 |
| …of those, actually synthesisable | 236 |
| synthesisable but unflagged | 6,071 |
| flagged correctly after the fix | 6,307 (all Gutenberg, nothing else) |

So 96.7% of the headphone badges were false, and the books that could
genuinely be narrated were the ones being denied. The flag drove a 🎧 badge
on `chatbot.html` and `questionnair.html` and the "Listen" button, so this
was a false claim shown to readers in three places, not an internal detail.

What actually decides it: `AudiobookEngine.generate` calls
`GutenbergClient.get_text(book_name)` — the text is fetched from Project
Gutenberg over the network, not read from the local `book_texts` table. Only
Gutenberg rows can be synthesised, which the catalogue already knows for
free. `chatbot.html`'s own help text had said "Generate audiobooks from
Project Gutenberg" all along; only the flag disagreed.

`audiobook_available()` now lives beside `price_and_availability` and
`price_within` in `services/catalogue.py`, shared by all three serialisers
for the reason those two are: when this logic sat inline, three builders gave
three different answers to one question, which is how F-36 and F-54 happened.
It reports **"can be made"**, not "exists" — a flag cannot know the latter
without stat-ing the disk for every row of every list response, and a stale
"exists" is a worse lie than an honest "available on request".

**Fault 2 — `dataset` values are strings.** The flag reached the player as
`opt.dataset.hasAudio = book.audiobook || false`, read back as
`dataset.hasAudio`. That yields the *string* `"false"`, which is truthy, so
`if (currentBook.hasAudio)` was true for every book regardless of the flag.
This is why the fallback looked universal rather than selective: every book,
Gutenberg or not, took the real-audio branch and hit the broken URL.

**Fault 3 — the URL, as reported.** `/audio/{id}` is not a registered route;
the real one is `/api/audiobook/{book_id}/stream`. The code comment admitted
the guess — *"Assuming the API serves audio at /audio/{id} or similar; adjust
URL based on your backend structure"* — and it was never adjusted. Every
request 404'd into a `catch` that called `startTTS()`, so the page always
played *something*. The failure was inaudible, which is why it survived.

Fixing the URL alone would have changed nothing observable: `audio_outputs/`
is empty, nothing has ever generated a file, and `audiobook_stream` 404s
until something does. `POST /api/audiobook/generate` was never called by
anything but its own tests — F-19 built the async job machinery (202, a poll
URL, a 15-minute result TTL) for a client that was never written. It exists
now, behind an explicit button: generation fetches from Gutenberg and
synthesises via gTTS, so it is a deliberate click, never something the page
does on load. 429 and 503 are reported as the deliberate refusals they are
rather than retried.

Two smaller things found in the same path and fixed with it: the
questionnaire linked to `./audiobook.html` while the file is
`Audiobook.html` — `StaticFiles` is case-sensitive on Linux, so "Listen"
worked only on the Windows dev box — and the `?book=<id>` that link passes
was ignored, landing the reader on an unselected dropdown of fifty others.

**The guard.** `tests/test_audiobook_availability.py` reads the player's
`${API_BASE}`-rooted URLs out of the page (code lines only; prose mentions
them too) and asserts each one against the app's OpenAPI paths. Verified
against the bug: reinstating `/audio/{id}` fails it with *"`/audio/{}` is not
a registered route. Registered audiobook routes: /api/audiobook/generate,
/api/audiobook/jobs/{}, /api/audiobook/{}, /api/audiobook/{}/stream"*. The
route table has to be read after startup and through the schema — the
routers are attached in the startup hook and wrapped in `_IncludedRouter`
objects with no `.path`, so a flat read of `app.routes` sees only the four
docs endpoints and would have passed while proving nothing.

**Still open.** F-18: no audio has ever been generated, so `AudiobookEngine`
itself remains unexercised outside its tests. The path to it is open now;
whether to actually run synthesis against a third-party TTS service is a
decision, not a fix.

