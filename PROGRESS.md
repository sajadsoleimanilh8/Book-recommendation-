# DigiKitab — Progress & Open Items

Running log of decisions, deferrals, and things that need a later look.
Full evidence for every `F-` reference is in [docs/PHASE-0-AUDIT.md](docs/PHASE-0-AUDIT.md).

---

## Phase status

| Phase | State | Notes |
|---|---|---|
| 0 — Audit | **Complete** (2026-08-19) | 20 findings. No production code modified (§73). |
| 1 — Foundation | **PR 1 open** on `pr/1-foundation` | 5 commits. Closes F-01…F-06, F-08, F-09, F-16, F-20(partial). |
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

**Not verified:** the app has never been booted end to end. `scikit-learn`,
`gtts` and `plyer` are absent from the validation environment, so
`import engine` fails there. **First task on review: install
`backend/requirements.txt` and confirm the app starts.** Note that pandas 3.x
and numpy 2.x are newer majors than this code was written against.

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

### OI-5 · Rate limit required before any deployment — **BLOCKING**
**Status (2026-08-19):** product owner confirmed the app is **local-only until further notice**, so the rate limit was deliberately **excluded from PR 1**.

**This is a deployment gate.** `/api/audiobook/generate` is unauthenticated (F-07) and synchronous (F-19). After PR 1 it can no longer destroy files, but each call still triggers an unbounded fetch-and-synthesise — repeated calls exhaust the server.

**Before the app becomes reachable from any network, ALL of:**
- per-IP rate limit on `/api/audiobook/generate`
- authentication (F-07)
- move generation to a background job (F-19)

Do not deploy, port-forward, expose via tunnel, or demo over a network until these land.

---

## Deferred technical debt

Carried deliberately, with the reason. Each has a closing phase.

| ID | Item | Why deferred | Closes in |
|---|---|---|---|
| F-07 | No authentication anywhere | Needs Postgres + `users`; too large for PR 1 | PR 2 / Phase 1 |
| F-19 | Synchronous TTS blocks the request | Needs Redis + job queue | Phase 6 |
| F-18 | Server-side desktop notifications (`plyer`) | Needs a real delivery channel + queue | Phase 6 |
| F-13 | LTR trained on constant features, circular target | Needs `recommendation_log` data first | Phase 3 |
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
