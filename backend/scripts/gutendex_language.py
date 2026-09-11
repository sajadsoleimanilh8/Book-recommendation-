"""Re-derive Gutenberg language labels from Gutenberg itself — F-48.

    python -m scripts.gutendex_language --dry-run
    python -m scripts.gutendex_language
    python -m scripts.gutendex_language --stats

The problem
-----------
Every one of the 6,307 Gutenberg rows is labelled `language = 'it'` in
`site_ready_books.json`, and `services.catalogue.normalize_language` turns
that into `"It"` (unmapped codes are `.capitalize()`d). Measured on 600 books
with stored text, scored on English versus Italian function words:
**600 English, 0 Italian.** Burroughs, Lovecraft and Conan Doyle are filed as
Italian.

This blocks F-47: an English-default language filter reading these labels
would exclude the only 6,307 books that have real descriptions and reading
text, and every test would still pass.

Why gutendex rather than the measurement
----------------------------------------
The function-word check is unambiguous, but 600 of 6,307 is a sample and
Project Gutenberg genuinely holds non-English texts. gutendex publishes the
real `languages` field per book, and every row already carries its Gutenberg
id as `external_id` — so this replaces a guess with a source of truth rather
than with a better guess.

Batched: `?ids=1,11,84,...` returns up to 100 books per request, so the whole
corpus costs ~65 requests rather than 6,307.

What this does NOT do on its own
--------------------------------
It corrects `books.language` in Postgres. The recommender builds its catalogue
from `site_ready_books.json`, so the corrected label only reaches ranking once
the catalogue loader prefers the database value — see
`services.catalogue.overlay_db_languages`. The first version of this pass
lacked that half, and would have fixed the database while leaving every
recommendation unchanged.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request

from sqlalchemy import func, select

from db import SessionLocal
from models import Book

log = logging.getLogger("gutendex_language")

ENDPOINT = "https://gutendex.com/books?ids={ids}"
USER_AGENT = "DigiKitab/1.0 (+https://github.com/digikitab) language-repair"
# gutendex returns 32 results per page. Asking for exactly that many keeps
# almost every batch to a single request — but correctness does not depend on
# it: fetch_languages follows `next` regardless, so a change to their page
# size costs requests, not books.
BATCH = 32
PAUSE_SECONDS = 0.4  # gutendex is a small volunteer service; do not hammer it
# Measured on 2026-09-11: 10 ids took 19s, 30 took 46s. 30s was too tight and
# turned slow-but-successful responses into failed batches.
TIMEOUT_SECONDS = 90
# A runaway `next` chain would otherwise loop forever against a volunteer
# service. 100 ids at 32 per page is 4 pages; this is generous.
MAX_PAGES = 10


def fetch_languages(ids: list[str]) -> dict[str, str] | None:
    """{gutenberg_id: iso_code} for one batch.

    Returns None when the request itself failed, which is distinct from an
    empty dict (request succeeded, no language recorded). The distinction is
    F-29's lesson: "we could not ask" must never be read as "the answer is
    nothing", or a network blip gets recorded as a fact.

    Follows gutendex's `next` links. The first version of this read page one
    only: gutendex returns 32 results per page, so a batch of 100 ids came
    back as 32, and the other 68 were counted as "not in gutendex" and left
    unrepaired — the corpus would have been about a third fixed while the run
    reported plausible numbers. Absent-as-negative again, one layer down: a
    book missing from page one is not a book missing from gutendex.

    If any page fails, the whole batch returns None, never a partial dict. A
    partial result would file the unfetched ids as "not in gutendex", which is
    the same mistake by another route.
    """
    url: str | None = ENDPOINT.format(ids=",".join(ids))
    out: dict[str, str] = {}
    pages = 0

    while url:
        pages += 1
        if pages > MAX_PAGES:
            log.warning(f"batch of {len(ids)} exceeded {MAX_PAGES} pages; treating as failed")
            return None
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning(
                f"batch of {len(ids)} failed on page {pages}: {type(exc).__name__}: {exc}"
            )
            return None

        for book in payload.get("results", []):
            languages = book.get("languages") or []
            if languages:
                out[str(book["id"])] = str(languages[0]).strip().lower()

        url = payload.get("next")
        if url:
            time.sleep(PAUSE_SECONDS)

    return out


def run(limit: int, dry_run: bool = False) -> dict:
    counts = {
        "checked": 0,
        "updated": 0,
        "unchanged": 0,
        "not_in_gutendex": 0,
        "batch_failed": 0,
    }
    changes: dict[str, int] = {}

    with SessionLocal() as session:
        rows = session.execute(
            select(Book.id, Book.external_id, Book.language)
            .where(Book.source == "gutenberg", Book.external_id.isnot(None))
            .order_by(Book.id)
            .limit(limit)
        ).all()
        log.info(f"{len(rows)} Gutenberg book(s) to check (dry_run={dry_run})")

        by_external = {str(ext).strip(): (pk, lang) for pk, ext, lang in rows}
        ids = list(by_external)

        for start in range(0, len(ids), BATCH):
            batch = ids[start : start + BATCH]
            found = fetch_languages(batch)
            counts["checked"] += len(batch)

            if found is None:
                # Leave these rows alone. They will be retried next run.
                counts["batch_failed"] += len(batch)
                time.sleep(PAUSE_SECONDS)
                continue

            for ext in batch:
                pk, current = by_external[ext]
                truth = found.get(ext)
                if truth is None:
                    counts["not_in_gutendex"] += 1
                    continue
                if (current or "").strip().lower() == truth:
                    counts["unchanged"] += 1
                    continue
                key = f"{current} -> {truth}"
                changes[key] = changes.get(key, 0) + 1
                counts["updated"] += 1
                if not dry_run:
                    session.get(Book, pk).language = truth

            if not dry_run:
                session.commit()
            if (start // BATCH) % 10 == 0:
                log.info(f"  {counts['checked']}/{len(ids)}  {counts}")
            time.sleep(PAUSE_SECONDS)

    return {**counts, "changes": changes}


def report() -> dict[str, int]:
    with SessionLocal() as session:
        rows = session.execute(
            select(Book.language, func.count())
            .where(Book.source == "gutenberg")
            .group_by(Book.language)
            .order_by(func.count().desc())
        ).all()
    return {str(lang): n for lang, n in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Gutenberg language labels.")
    parser.add_argument("--limit", type=int, default=7000)
    parser.add_argument("--dry-run", action="store_true", help="report changes, write nothing")
    parser.add_argument("--stats", action="store_true", help="report only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    if not args.stats:
        for key, value in run(args.limit, dry_run=args.dry_run).items():
            print(f"  {key:18} {value}")
    print("gutenberg languages:")
    for lang, n in report().items():
        print(f"  {lang:<12} {n:>7,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
