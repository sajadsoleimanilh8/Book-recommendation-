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
from datetime import datetime, timezone

from sqlalchemy import case, func, select

import config
import net_cache
from db import SessionLocal
from models import Book, catalogue_only
from providers import GoogleBooksProvider, OpenLibraryProvider, QuotaExceeded, reconcile
from providers.base import ProviderThrottled
from providers.reconcile import MIN_DESCRIPTION_CHARS, _usable_description

log = logging.getLogger("enrich")

STATUS_PENDING = "pending"
STATUS_OK = "ok"
STATUS_PARTIAL = "partial"      # found the book, but no usable description
STATUS_NOT_FOUND = "not_found"
STATUS_FAILED = "failed"

# Circuit breaker. Google Books answers 503 rather than 429 when it is
# refusing sustained traffic, so "throttled" and "briefly unwell" look
# identical. A handful of consecutive throttles is the signal to stop: past
# that point every further book costs its full retry ladder and returns
# nothing. Observed live at ~285 books into a run.
THROTTLE_LIMIT = 3


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

    counts: dict[str, int] = {}
    processed = 0
    quota_hit = False
    throttled = False
    consecutive_throttles = 0

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
                consecutive_throttles += 1
                log.warning(
                    f"provider throttled ({consecutive_throttles}/{THROTTLE_LIMIT}): {exc}"
                )
                if consecutive_throttles >= THROTTLE_LIMIT:
                    log.error(
                        f"Provider is refusing traffic after {processed} book(s). "
                        "Stopping; progress is saved. Resume later."
                    )
                    throttled = True
                    break
                continue  # leave this book pending; do not mark it failed
            except Exception as exc:  # a single bad record must not end the run
                log.warning(f"book {book.id} ({book.title[:40]!r}) failed: {exc}")
                status, source = STATUS_FAILED, None

            if not dry_run:
                book.enrichment_status = status
                book.enrichment_source = source
                book.enriched_at = datetime.now(timezone.utc)

            consecutive_throttles = 0
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
        )
        print("run:")
        for k, v in sorted(result.items()):
            print(f"  {k:22} {v}")
        if result.get("quota_exhausted"):
            print("\n  Daily quota exhausted. Progress is saved; resume tomorrow.")

    print("catalogue:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
