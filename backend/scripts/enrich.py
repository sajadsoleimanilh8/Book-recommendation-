"""Catalogue enrichment — Phase 2, the critical path.

Fills in the descriptions that F-15 found missing for 100% of the catalogue,
and the ISBNs that reconciliation and the F-25 duplicate recheck both need.

    python -m scripts.enrich --limit 200          # bounded run
    python -m scripts.enrich --stats              # report only, no requests
    python -m scripts.enrich --limit 500 --dry-run

**Quota is the binding constraint.** The Google Books free tier is roughly
1,000 queries/day and there are 28,399 books, so a full pass is measured in
weeks, not hours. Everything here follows from that:

* **Resumable.** `books.enrichment_status` is committed per book, so a run
  that dies at book 9,000 resumes at 9,001. With this quota, losing progress
  is not a recoverable mistake.
* **Prioritised.** English first (OI-2), then unlabelled, then the rest, and
  within each group the most-rated books first — those are the ones users
  actually see, so early quota buys the most visible improvement.
* **Stops cleanly on quota.** `QuotaExceeded` aborts the whole run rather
  than marking one book failed and spending the remaining budget on calls
  that cannot succeed.
* **Open Library first where it can answer.** It costs no Google quota.

Nothing is invented. A book neither provider knows is marked `not_found`,
not filled with a plausible-sounding description.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from sqlalchemy import case, func, select

import config
import net_cache
from db import SessionLocal
from models import Book, catalogue_only
from providers import GoogleBooksProvider, OpenLibraryProvider, QuotaExceeded, reconcile
from providers.base import ProviderThrottled, ProviderUnreachable, probe
from providers.reconcile import MIN_DESCRIPTION_CHARS, _usable_description

log = logging.getLogger("enrich")

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"      # found the book, but no usable description
STATUS_NOT_FOUND = "not_found"
STATUS_FAILED = "failed"

# Circuit breaker. Google Books answers 503 rather than 429 when it is
# refusing sustained traffic, so "throttled" and "briefly unwell" look
# identical. A handful of throttles is the signal to stop: past that point
# every further book costs its full retry ladder and returns nothing.
# Observed live at ~285 books into a run.
THROTTLE_LIMIT = 3

# F-65: three strikes alone was not the right condition. A 403 is raised
# without retries — correctly, since retrying a real 403 burns quota to learn
# nothing — so three of them arrive back to back in about six seconds, and
# every nightly pass ended before its first book. The breaker now needs
# *sustained* refusal: three strikes that span at least THROTTLE_MIN_SPAN,
# with a backoff between each. A burst of instant failures no longer ends
# the day's pass; a provider that is genuinely down still stops it.
THROTTLE_BACKOFF = (5.0, 20.0, 60.0, 120.0)
THROTTLE_MIN_SPAN = 120.0

# ...and a bound on the other side: a provider that fails every other book
# would otherwise sleep its way through the night making no progress. Once
# this much has been spent waiting, stop and resume later.
THROTTLE_SLEEP_BUDGET = 600.0

# Preflight. The stall was not a provider problem at all: the task fires on
# wake rather than at 04:00, so its first requests go out over a network
# that is not up yet. Ask once, cheaply, before touching the first book.
PREFLIGHT_DELAYS = (15.0, 30.0, 60.0, 120.0)


def preflight(
    google,
    openlib,
    *,
    delays: tuple[float, ...] = PREFLIGHT_DELAYS,
    sleeper=time.sleep,
) -> dict:
    """Ask whether the providers are reachable before spending a pass on it.

    F-65 (a). Returns ``{"reachable": bool, "google": bool, "waited": float,
    "detail": str | None}``.

    Open Library is asked first because it costs nothing and because it is
    the better question: if even a free, unauthenticated search cannot
    complete, the network is not up and no provider will answer. That is the
    actual F-65 failure, and it resolves itself within a minute or two of
    wake — so it is worth waiting out rather than giving up on.

    A Google-specific refusal is handled differently. If Open Library answers
    and Google does not, waiting will not help: that is a key, a quota or
    something standing in front of the API, none of which a sleep fixes. The
    run continues on Open Library alone — which still fills ISBNs, years and
    older descriptions — rather than doing nothing at all. The refusal is
    logged in full so the cause is readable afterwards instead of needing
    another five days of elimination.
    """
    waited = 0.0
    last = None

    for attempt in range(1, len(delays) + 2):
        try:
            probe(openlib.probe_url())
        except ProviderUnreachable as exc:
            last = str(exc)
            if attempt > len(delays):
                break
            delay = delays[attempt - 1]
            log.warning(
                f"preflight {attempt}/{len(delays) + 1}: network not ready "
                f"({last}); waiting {delay:.0f}s"
            )
            sleeper(delay)
            waited += delay
            continue

        # The network is up. Now the question is only about Google.
        if not google.configured:
            return {"reachable": True, "google": False, "waited": waited, "detail": None}

        try:
            probe(google.probe_url())
        except QuotaExceeded as exc:
            # A real answer, not a cold network. Nothing to wait for.
            log.error(f"preflight: Google Books quota already gone — {exc}")
            return {
                "reachable": True,
                "google": False,
                "waited": waited,
                "detail": str(exc),
            }
        except ProviderUnreachable as exc:
            log.error(
                "preflight: Open Library answers but Google Books refuses "
                f"— {exc}"
            )
            log.warning(
                "Continuing on Open Library only. This is not a cold network: "
                "check the key, the quota and whether something is proxying "
                "www.googleapis.com."
            )
            return {
                "reachable": True,
                "google": False,
                "waited": waited,
                "detail": str(exc),
            }

        if waited:
            log.info(f"preflight: both providers reachable after {waited:.0f}s")
        return {"reachable": True, "google": True, "waited": waited, "detail": None}

    log.error(
        f"preflight: no provider reachable after {waited:.0f}s of waiting "
        f"({last}). Doing nothing; no book is marked. Resume later."
    )
    return {"reachable": False, "google": False, "waited": waited, "detail": last}


def priority_order():
    """English first, then unlabelled, then everything else (OI-2); within
    each group, most-rated first."""
    language_rank = case(
        (Book.language == "en", 0),
        (Book.language.is_(None), 1),
        else_=2,
    )
    return language_rank, Book.ratings_count.desc(), Book.id


def pending_query(
    limit: int,
    languages: list[str] | None = None,
    sources: list[str] | None = None,
):
    # Phase 4: catalogue only. `sources` below defaults to None, meaning
    # "every source", so without this an uploaded book would be queued and
    # its title and author sent to Google Books — a third party learning
    # what is in a reader's private library, paid for out of the quota that
    # is this project's binding constraint.
    stmt = catalogue_only(
        select(Book).where(Book.enrichment_status == STATUS_PENDING)
    )
    if languages:
        stmt = stmt.where(Book.language.in_(languages))
    if sources:
        stmt = stmt.where(Book.source.in_(sources))
    return stmt.order_by(*priority_order()).limit(limit)


def enrich_one(book: Book, google, openlib) -> tuple[str, str | None]:
    """Enrich a single book in place. Returns (status, source).

    Raises QuotaExceeded, which the caller must treat as fatal for the run.
    """
    title, author = book.title, book.author

    # Google Books first: it is the better description source, and for the
    # 13,826 rows that came from Google we already hold an exact volume id,
    # which is a precise lookup rather than a fuzzy search.
    gb = None
    if google.configured:
        if book.source == "google_books" and book.external_id:
            gb = google.get_book(book.external_id)
        if gb is None:
            gb = google.find_for_book(title, author)

    # Open Library costs no Google quota, so it is always worth asking —
    # especially for the older titles where Google coverage thins.
    ol = openlib.find_for_book(title, author)

    merged, provenance = reconcile(gb, ol)
    if merged is None:
        return STATUS_NOT_FOUND, None

    description = _usable_description(merged.description)
    if description:
        book.description = description
    if merged.isbn_13:
        book.isbn_13 = merged.isbn_13
    if merged.isbn_10:
        book.isbn_10 = merged.isbn_10
    if merged.language and not book.language:
        book.language = merged.language
    if merged.page_count and not book.page_count:
        book.page_count = int(merged.page_count)
    if merged.published_year and not book.published_year:
        book.published_year = int(merged.published_year)
    if merged.thumbnail and not book.thumbnail:
        book.thumbnail = merged.thumbnail
    if merged.categories and book.genre in (None, "", "Unknown"):
        book.genre = merged.categories[0]

    # "partial" is deliberate: we found the book and learned something (an
    # ISBN, a year), but not the description Phase 2 exists for. Keeping it
    # distinct from "ok" means the embedding step can select only rows it can
    # actually embed, and a later pass can retry just the partials.
    return (STATUS_OK if description else STATUS_PARTIAL), provenance


def run(
    limit: int,
    dry_run: bool = False,
    languages: list[str] | None = None,
    sources: list[str] | None = None,
    use_google: bool = True,
    skip_preflight: bool = False,
    sleeper=time.sleep,
) -> dict:
    # use_google=False spends no Google quota at all. Open Library is
    # strongest on older public-domain titles, which is exactly the Gutenberg
    # subset, so that pass is free and the Google budget stays intact for the
    # modern Goodreads/Google records where Open Library is weak.
    google = GoogleBooksProvider(api_key="" if not use_google else None)
    openlib = OpenLibraryProvider()

    if use_google and not google.configured:
        log.warning(
            "GOOGLE_BOOKS_API_KEY is not set — running on Open Library only. "
            "Anonymous Google Books requests are quota-exhausted in practice."
        )

    # F-65 (a): ask before working. The failing pass did its asking one book
    # at a time, against a network that was not up, and read the result as a
    # provider refusing it.
    pre = {"reachable": True, "google": google.configured, "waited": 0.0, "detail": None}
    if not skip_preflight:
        pre = preflight(google, openlib, sleeper=sleeper)
        if not pre["reachable"]:
            # Nothing is marked, nothing is spent. A pass that cannot reach
            # anything should leave no trace but a log line saying why.
            return {
                "processed": 0,
                "quota_exhausted": False,
                "throttled": False,
                "unreachable": True,
                "preflight_waited": round(pre["waited"], 1),
                "preflight_detail": pre["detail"],
                "dry_run": dry_run,
            }
        if use_google and google.configured and not pre["google"]:
            # Google is out for this pass but Open Library answered. Drop to
            # the keyless provider so `enrich_one` skips it cleanly rather
            # than re-discovering the refusal on every book.
            google = GoogleBooksProvider(api_key="")

    counts: dict[str, int] = {}
    processed = 0
    quota_hit = False
    throttled = False
    throttle_strikes: list[float] = []
    throttle_slept = 0.0

    with SessionLocal() as session:
        books = list(session.scalars(pending_query(limit, languages, sources)))
        log.info(
            f"{len(books)} book(s) queued "
            f"(google={'on' if google.configured else 'OFF'}, "
            f"sources={sources or 'all'}, languages={languages or 'all'})"
        )

        for book in books:
            try:
                status, source = enrich_one(book, google, openlib)
            except QuotaExceeded as exc:
                log.error(f"Daily quota exhausted after {processed} book(s): {exc}")
                quota_hit = True
                break
            except ProviderThrottled as exc:
                now = time.monotonic()
                throttle_strikes.append(now)
                span = now - throttle_strikes[0]
                log.warning(
                    f"provider throttled ({len(throttle_strikes)}/{THROTTLE_LIMIT}, "
                    f"spanning {span:.0f}s): {exc}"
                )

                # F-65 (b): the count alone is not the condition. Three 403s
                # arrive in six seconds because 403 is raised without
                # retries, and that burst is what ended every nightly pass
                # before its first book. Tripping now needs both enough
                # strikes *and* enough elapsed time for them to mean
                # "sustained" rather than "simultaneous".
                sustained = (
                    len(throttle_strikes) >= THROTTLE_LIMIT
                    and span >= THROTTLE_MIN_SPAN
                )
                if sustained:
                    log.error(
                        f"Provider has refused traffic for {span:.0f}s across "
                        f"{len(throttle_strikes)} strikes, after {processed} "
                        "book(s). Stopping; progress is saved. Resume later."
                    )
                    throttled = True
                    break

                if throttle_slept >= THROTTLE_SLEEP_BUDGET:
                    # The other bound: a provider failing intermittently
                    # would otherwise spend the night asleep for nothing.
                    log.error(
                        f"Spent {throttle_slept:.0f}s waiting on throttles "
                        f"after {processed} book(s). Stopping; progress is "
                        "saved. Resume later."
                    )
                    throttled = True
                    break

                delay = THROTTLE_BACKOFF[
                    min(len(throttle_strikes), len(THROTTLE_BACKOFF)) - 1
                ]
                log.info(f"  backing off {delay:.0f}s before the next book")
                sleeper(delay)
                throttle_slept += delay
                continue  # leave this book pending; do not mark it failed
            except Exception as exc:  # a single bad record must not end the run
                log.warning(f"book {book.id} ({book.title[:40]!r}) failed: {exc}")
                status, source = STATUS_FAILED, None

            if not dry_run:
                book.enrichment_status = status
                book.enrichment_source = source
                book.enriched_at = datetime.now(timezone.utc)

            # A book that worked clears the strikes: whatever the provider
            # was doing, it is answering now.
            throttle_strikes.clear()
            counts[status] = counts.get(status, 0) + 1
            processed += 1

            # Commit in batches so an interrupted run keeps its progress.
            if not dry_run and processed % 25 == 0:
                session.commit()
                log.info(f"  {processed}/{len(books)}  {counts}")

        if not dry_run:
            session.commit()

    return {
        "processed": processed,
        "quota_exhausted": quota_hit,
        "throttled": throttled,
        "unreachable": False,
        "google_used": google.configured,
        "preflight_waited": round(pre["waited"], 1),
        "throttle_slept": round(throttle_slept, 1),
        "dry_run": dry_run,
        **counts,
    }


def report() -> dict:
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(Book)) or 0
        if not total:
            return {"books": 0}

        by_status = dict(
            session.execute(
                catalogue_only(
                    select(Book.enrichment_status, func.count())
                ).group_by(Book.enrichment_status)
            ).all()
        )
        with_desc = session.scalar(
            select(func.count()).select_from(Book).where(Book.description.isnot(None))
        )
        with_isbn = session.scalar(
            select(func.count()).select_from(Book).where(Book.isbn_13.isnot(None))
        )
        english_pending = session.scalar(
            select(func.count())
            .select_from(Book)
            .where(Book.language == "en", Book.enrichment_status == STATUS_PENDING)
        )
        return {
            "books": total,
            "by_status": by_status,
            "with_description": with_desc,
            "description_coverage": f"{with_desc / total:.2%}",
            "with_isbn_13": with_isbn,
            "isbn_coverage": f"{with_isbn / total:.2%}",
            "english_still_pending": english_pending,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich the book catalogue.")
    parser.add_argument("--limit", type=int, default=100, help="max books this run")
    parser.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    parser.add_argument("--stats", action="store_true", help="report only, no requests")
    parser.add_argument("--language", action="append", help="restrict to a language code")
    parser.add_argument(
        "--source", action="append",
        help="restrict to a catalogue source (gutenberg | goodreads | google_books)",
    )
    parser.add_argument(
        "--no-google", action="store_true",
        help="Open Library only — spends no Google Books quota",
    )
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="go straight to the first book (F-65 preflight is on by default)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    # F-32: without this every request pays a fresh DNS lookup, and the
    # resolver starts refusing under the load of a long parallel run — which
    # reads as the provider blocking us when it is self-inflicted.
    net_cache.install()

    if not args.stats:
        result = run(
            args.limit,
            dry_run=args.dry_run,
            languages=args.language,
            sources=args.source,
            use_google=not args.no_google,
            skip_preflight=args.skip_preflight,
        )
        print("run:")
        for k, v in sorted(result.items()):
            print(f"  {k:22} {v}")
        if result.get("quota_exhausted"):
            print("\n  Daily quota exhausted. Progress is saved; resume tomorrow.")
        if result.get("unreachable"):
            # Exit non-zero so a scheduled run that reached nothing is
            # distinguishable from one that did the work. F-65 was invisible
            # partly because "Last Result: 0" said success either way.
            print("\n  No provider reachable. Nothing was marked; resume later.")
            return 2

    print("catalogue:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
