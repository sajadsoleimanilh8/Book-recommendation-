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
