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
| 2 — Enrichment | **In progress** on `phase-2-enrichment` | Passes died on a Docker stop at 00:18; found F-29, F-30. Found F-27, F-28. Closed F-17. |

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

### OI-11 · What should a price filter do when no price is known? — **NEEDS DECISION**
See F-36. `max_price` currently treats unknown as 0, so "under $5" returns
everything. Excluding unknown-price books is accurate but returns an empty
list until an availability provider exists. My recommendation: hide the price
filter in the UI while `availability` is unknown for the whole catalogue,
rather than offering a control that cannot work.

### OI-10 · Should an invalid token 401, or degrade to anonymous? — **NEEDS DECISION**
See F-34. Current behaviour is silent degradation on every `OptionalUser`
route. The security-correct answer is 401, but it changes behaviour across the
API and the frontend must then handle expiry. My recommendation: 401 for a
*malformed or forged* token, and a distinct, explicit response for an
*expired* one so the frontend can refresh rather than log the user out.

### OI-9 · Install sentence-transformers for real embeddings? — **NEEDS DECISION**
`torch` is already present, so the install is small, but it resolves
dependencies inside a shared global Python (F-24) while an unrelated training
job is running (F-33) and could move `numpy` or `torch` underneath it. Search
currently runs on TF-IDF+SVD, which works and cost nothing. Switching later is
`EMBEDDING_BACKEND=minilm` plus `embed_pass --redo` — the seam is already
there. **Recommendation:** wait until that training run finishes, then install
into a virtualenv rather than the global interpreter.

### OI-8 · Windows sleep is the root cause of the lost runs — **HOST SETTING, NOT MINE TO CHANGE**
The containers now restart with the daemon and a supervisor relaunches dead
passes, but neither prevents the host from sleeping mid-run. Long unattended
passes will keep being interrupted until the power plan is changed
(`powercfg /change standby-timeout-ac 0`) or the runs move to a machine that
stays awake. Flagged rather than changed: altering a developer's power
settings is not a repository decision.

### OI-7 · Google Books quota is the binding constraint — **NEEDS DECISION**
Measured: ~1,000 queries/day against 12,786 remaining English records, one to
two queries each. That is two to four weeks of wall-clock at the current
allowance. See F-31. If the increase does not land, the choice is between
Open Library-only coverage for the English set or a standing ~800/day job.

### OI-6 · Frontend needs a login UI — OPEN
Comments, progress and reminders all require a token now, so the existing
pages get 401. Expected and accepted; lands with the Express retirement.

### OI-5 · Rate limit required before any deployment — **BLOCKING**
**Status (2026-08-19):** product owner confirmed the app is **local-only until further notice**, so the rate limit was deliberately **excluded from PR 1**.

**This is a deployment gate.** `/api/audiobook/generate` is unauthenticated (F-07) and synchronous (F-19). After PR 1 it can no longer destroy files, but each call still triggers an unbounded fetch-and-synthesise — repeated calls exhaust the server.

**Before the app becomes reachable from any network, ALL of:**
- ~~authentication (F-07)~~ — **done in PR 2**
- ~~read-authz sweep~~ — **done in PR 3**
- per-IP rate limit on `/api/audiobook/generate` — still open
- move generation to a background job (F-19) — still open
- `JWT_SECRET` set in the environment (config refuses to boot without it
  when `ENV=production`; the `.dev-jwt-secret` fallback is development-only)
- token revocation (still no denylist) — accepted risk, revisit with Redis

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

**Retrieval quality, measured rather than eyeballed** (`python -m eval_search`):

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

**Deferred with a measurement:** at 29k vectors a full scan costs 66 ms, so no
ANN index yet (§8). Worth revisiting past ~150k chunks, where the same scan
would cost roughly 380 ms.

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

### F-34 · An invalid token silently becomes an anonymous request — OPEN, **NEEDS DECISION**

`get_current_user_optional` returns `None` for a forged or expired token in
exactly the same way it does for no token at all. So a caller with a bad token
gets **200 with anonymous results** rather than 401.

For semantic search this is more than cosmetic: a user whose token expired
silently stops seeing their own private passages, and nothing tells them why.
The search looks like it worked and quietly returned less.

Not fixed here, deliberately. `OptionalUser` is shared by every read endpoint,
so changing it means expired tokens start returning 401 across the API and the
frontend has to handle that — a product decision about session expiry
behaviour, not a patch to make inside a search PR (§2, one deliberate change
at a time).

Logged as an `xfail(strict=True)` in `test_search_endpoint.py`, the same
pattern used for F-22 and F-26: the moment somebody fixes it, the suite says
so loudly. Logged as OI-10.

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

### F-31 · The Google pass is quota-bound, not code-bound — **NEEDS PRODUCT DECISION**

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

**Decision needed:** if the increase is declined or slow, the fallback is to
accept Open Library-only coverage for the English set (weaker on modern
titles) or to pace Google at ~800 books/day as a standing background job.
Logged as OI-7.

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
| ~~F-07~~ | ~~No authentication anywhere~~ | **Closed** — writes in PR 2, reads in PR 3 | ✅ |
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
