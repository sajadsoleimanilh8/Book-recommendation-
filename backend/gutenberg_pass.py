"""Fetch descriptions and reading text for Gutenberg books — Phase 2.

    python -m gutenberg_pass --limit 200
    python -m gutenberg_pass --stats

Why this exists rather than more Open Library
---------------------------------------------
Measured: Open Library yields a usable description for only **15%** of the
Gutenberg subset. It finds the books and holds no blurb. Meanwhile these
*are* Gutenberg texts, so the opening prose of the book itself is available
for free — and it is better embedding material than a blurb would have been,
because it is the work's actual voice rather than ad copy.

Costs, measured
---------------
    ~5s per book        exact fetch by id, no search step
    ~40 KB per book     HTTP Range, verified 206 (full texts would be ~3 GB)
    0 Google quota      runs in parallel with the quota-funded English pass

Section 11: Gutenberg only. Every row records its source so the licensing
basis is explicit and auditable.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from db import SessionLocal
from models import Book, BookText
from providers.base import RateLimiter
from providers.gutenberg_text import extract_description, extract_reading_text, fetch_opening

log = logging.getLogger("gutenberg_pass")

SOURCE = "gutenberg"


def pending_query(limit: int, redo: bool = False):
    """Gutenberg books that still lack real content.

    Independent of `enrichment_status`, because the Open Library pass may
    already have marked a book `partial` (it found metadata but no
    description). Those are exactly the rows this pass exists to finish.
    """
    stmt = select(Book).where(Book.source == SOURCE)
    if not redo:
        stmt = stmt.where(
            Book.id.notin_(select(BookText.book_id))
        )
    return stmt.order_by(Book.ratings_count.desc(), Book.id).limit(limit)


def process_one(session, book: Book) -> str:
    raw = fetch_opening(book.external_id)
    if not raw:
        return "unavailable"

    reading_text = extract_reading_text(raw)
    description = extract_description(raw)

    if not reading_text and not description:
        return "no_content"

    if reading_text:
        stmt = insert(BookText).values(
            book_id=book.id,
            source=SOURCE,
            content=reading_text,
            char_count=len(reading_text),
            is_complete=False,
        )
        # Idempotent: re-running refreshes rather than failing.
        stmt = stmt.on_conflict_do_update(
            index_elements=[BookText.book_id],
            set_={
                "content": stmt.excluded.content,
                "char_count": stmt.excluded.char_count,
                "source": stmt.excluded.source,
            },
        )
        session.execute(stmt)

    if description and not book.description:
        # Never overwrite a real blurb from a metadata provider — that is
        # editorial copy written to describe the book, which is a better
        # description even though the opening prose is better embedding
        # material. Only fill the gap.
        book.description = description
        book.enrichment_source = "gutenberg_text"
        book.enriched_at = datetime.now(timezone.utc)
        if book.enrichment_status in ("pending", "partial", "not_found"):
            book.enrichment_status = "ok"

    return "ok" if description else "text_only"


def run(limit: int, redo: bool = False) -> dict:
    # Gutenberg is a donation-funded archive. Space requests out rather than
    # hammering it; the pass is unattended, so slower costs nothing.
    limiter = RateLimiter(0.4)
    counts: dict[str, int] = {}
    processed = 0

    with SessionLocal() as session:
        books = list(session.scalars(pending_query(limit, redo)))
        log.info(f"{len(books)} Gutenberg book(s) queued")

        for book in books:
            limiter.wait()
            try:
                status = process_one(session, book)
            except Exception as exc:
                log.warning(f"book {book.id} ({book.title[:40]!r}): {exc}")
                status = "failed"

            counts[status] = counts.get(status, 0) + 1
            processed += 1

            if processed % 25 == 0:
                session.commit()
                log.info(f"  {processed}/{len(books)}  {counts}")

        session.commit()

    return {"processed": processed, **counts}


def report() -> dict:
    with SessionLocal() as session:
        total = session.scalar(
            select(func.count()).select_from(Book).where(Book.source == SOURCE)
        ) or 0
        with_text = session.scalar(select(func.count()).select_from(BookText)) or 0
        with_desc = session.scalar(
            select(func.count())
            .select_from(Book)
            .where(Book.source == SOURCE, Book.description.isnot(None))
        ) or 0
        chars = session.scalar(select(func.coalesce(func.sum(BookText.char_count), 0)))
        return {
            "gutenberg_books": total,
            "with_reading_text": with_text,
            "with_description": with_desc,
            "description_coverage": f"{(with_desc / total if total else 0):.1%}",
            "stored_text_mb": round((chars or 0) / 1_000_000, 1),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Gutenberg text content.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--redo", action="store_true", help="refetch books already stored")
    parser.add_argument("--stats", action="store_true", help="report only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if not args.stats:
        result = run(args.limit, redo=args.redo)
        print("run:")
        for k, v in sorted(result.items()):
            print(f"  {k:22} {v}")

    print("gutenberg:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
