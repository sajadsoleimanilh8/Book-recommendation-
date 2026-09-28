# Tests

```bash
python -m pytest tests/ -q
```

## Why these tests look the way they do

They are **deliberately dependency-light**. They assert against source text
and validation boundaries rather than booting a live app.

That is a compromise, and it is worth understanding before extending them:
`scikit-learn`, `gtts` and `plyer` were not installed in the environment
where PR 1 was written, so `import engine` — and therefore `import main` —
fails there. A suite that needed a running app could not have been written
or verified at all.

The consequence: **these tests prove the vulnerabilities are closed at the
boundary, not that the app works end to end.** They would not catch a
runtime failure inside `Recommender.fit()`.

## What to do once the full stack installs

Replace the source-text assertions with real ones, in this order:

1. `fastapi.testclient.TestClient` against the real app.
2. `POST /api/audiobook/generate {"output_file": "../server.js"}` → assert
   422 **and** assert `server.js` is unmodified on disk. That is the true
   form of `test_output_file_is_rejected`.
3. `GET /health` → assert `books_loaded == 29975` and `data_source == "json"`.
4. `GET /api/books/filter-options` → assert 200, not 422. That is the true
   form of the F-16 fix.
5. Golden-output tests for the questionnaire and recommender **before** any
   Phase 3 work touches ranking (§4, §62).

Item 5 is the important one. The audit found the LTR model is trained on
constant features against a circular target (F-13). Fixing that will change
ranking output, and without a golden baseline recorded first there is no way
to tell an improvement from a regression.

## The one test that is supposed to fail eventually

`test_missing_descriptions_baseline` asserts description coverage is
**exactly 0%** — the current, broken state. When Phase 2 enrichment starts,
it will fail. That failure is the signal to raise the threshold
deliberately, not a bug. It exists so the number cannot drift unnoticed in
either direction.

---

## Update — 2026-08-19: the full stack now installs

`test_app_boots.py` is the "true form" suite described above. It boots the
real FastAPI app with the real 29,975-book catalogue and asserts:

- `/health` reports `ok`, 29,975 books, and every sub-engine ready
- the F-04 exploit returns 422 **and** `server.js` md5 is unchanged
- `/api/books/filter-options` returns 200, not 422 (F-16)
- recommendations and the questionnaire return real, non-synthetic titles
- `/api/audiobook/{id}/stream` 404s for an ungenerated book (F-06)

It `importorskip`s scikit-learn and gtts, so it degrades to a skip rather
than a failure where the ML stack is absent. The source-text assertions in
the other two modules stay as a cheap first line of defence that runs
anywhere.

**Still outstanding from the list above:** golden-output tests for the
questionnaire and recommender, which must be recorded before any Phase 3
work touches ranking.

### The xfail

`test_chatbot_returns_recommendations` is `xfail(strict=True)` — F-22, the
chatbot never populates its `books` list. Strict means it fails the suite if
it ever *passes*, so whoever fixes F-22 is forced to remove the marker rather
than leave a stale one behind.

---

## Update — 2026-09-26: both of the above happened; this file did not say so

Found while auditing the top-level `README.md` for unrelated staleness and
checking whether this file had the same problem. It did, in the two most
consequential places:

**The xfail is gone.** F-22 was fixed well before this update — the AI
Librarian's tool loop returns real, grounded books — and
`test_chatbot_returns_recommendations` no longer carries the marker at all
(its own docstring: *"was xfail(strict=True) while the chatbot was an
intent classifier with canned replies... leaving a strict xfail in place
would have failed the suite the moment the fix worked, which is what
strict is for"*). Exactly the mechanism this file predicted, having
actually fired.

**The golden baselines exist**, have existed since well before this
update, and have been regenerated more than once since (most recently
2026-09-26, after a restore following unrelated data loss — see
`docs/PROGRESS.md`, F-62). "Still outstanding" above is the one line in
this file most likely to send a reader down the wrong path: building a
second golden-baseline mechanism believing none exists.

**The suite itself has grown far past what this file describes.** 500+
tests as of Phase 4, most requiring a real Postgres instance and the full
ML stack (`docker compose up -d`, then a `.venv` per the top-level
README), not the dependency-light, source-text-assertion style this file's
opening sections describe as the whole approach. That style still exists
in a handful of files — the ones this file's original text was written
about — but it is the minority now, not the plan.

None of this is corrected by editing the sections above: they are an
accurate record of PR 1's actual constraints and reasoning at the time, the
same reason `docs/PHASE-0-AUDIT.md` is kept as a dated snapshot rather than
rewritten. This update exists so a reader does not have to independently
discover which parts are history and which parts are still the plan.
