"""Populate book_chunks from stored book text — sections 22 and 27.

    python -m chunk_pass --limit 500
    python -m chunk_pass --stats

Separate from the fetch pass on purpose. Fetching is network-bound and slow;
chunking is pure CPU over text already on disk, so it can run whenever, be
re-run after a tuning change, and never costs a provider request.

Only public catalogue text is chunked here. Private user uploads take the same
`chunk_text` path but are written with `visibility='private'` and an owner,
which is Phase 4 work (section 29).
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError

from chunking import chunk_text
from db import SessionLocal
from models import BookChunk, BookText

log = logging.getLogger("chunk_pass")


def pending_query(limit: int, redo: bool):
    """Books with stored text but no public chunks yet."""
    already = select(BookChunk.book_id).where(BookChunk.visibility == "public")
    stmt = select(BookText.book_id, BookText.content)
    if not redo:
        stmt = stmt.where(BookText.book_id.notin_(already))
    return stmt.order_by(BookText.book_id).limit(limit)


def chunk_one(session, book_id: int, content: str) -> int:
    """Replace this book's public chunks. Returns the number written."""
    pieces = chunk_text(content)
    if not pieces:
        return 0

    # Delete-then-insert rather than upsert: a tuning change alters both the
    # count and the boundaries, so matching old ordinals to new ones is
    # meaningless. Private chunks are untouched — this filters on visibility.
    session.execute(
        delete(BookChunk).where(
            BookChunk.book_id == book_id, BookChunk.visibility == "public"
        )
    )
    session.add_all(
        BookChunk(
            book_id=book_id,
            user_id=None,
            visibility="public",
            ordinal=i,
            content=piece,
            char_count=len(piece),
        )
        for i, piece in enumerate(pieces)
    )
    return len(pieces)


def run(limit: int, redo: bool = False) -> dict:
    written = books = 0
    empty = 0

    with SessionLocal() as session:
        rows = session.execute(pending_query(limit, redo)).all()
        log.info(f"{len(rows)} book text(s) queued")

        for book_id, content in rows:
            try:
                n = chunk_one(session, book_id, content)
            except OperationalError as exc:
                # F-30: the database went away. Every remaining book fails the
                # same way, so stop rather than logging thousands of copies.
                session.rollback()
                log.error(f"database unreachable after {books} book(s): {exc}")
                break
            except Exception as exc:
                session.rollback()
                log.warning(f"book {book_id}: {exc}")
                continue

            if n == 0:
                empty += 1
            else:
                written += n
                books += 1

            if (books + empty) % 100 == 0:
                session.commit()
                log.info(f"  {books + empty}/{len(rows)} books, {written} chunks")

        session.commit()

    return {"books_chunked": books, "chunks_written": written, "no_content": empty}


def report() -> dict:
    with SessionLocal() as session:
        texts = session.scalar(select(func.count()).select_from(BookText)) or 0
        chunks = session.scalar(select(func.count()).select_from(BookChunk)) or 0
        chunked_books = session.scalar(
            select(func.count(func.distinct(BookChunk.book_id)))
        ) or 0
        unembedded = session.scalar(
            select(func.count()).select_from(BookChunk).where(BookChunk.embedding.is_(None))
        ) or 0
        chars = session.scalar(
            select(func.coalesce(func.sum(BookChunk.char_count), 0))
        ) or 0
        return {
            "book_texts": texts,
            "books_chunked": chunked_books,
            "books_pending": max(0, texts - chunked_books),
            "chunks_total": chunks,
            "chunks_unembedded": unembedded,
            "mean_chunks_per_book": round(chunks / chunked_books, 1) if chunked_books else 0,
            "chunk_text_mb": round(chars / 1_000_000, 1),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Chunk stored book text.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--redo", action="store_true", help="rechunk books already done")
    parser.add_argument("--stats", action="store_true", help="report only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if not args.stats:
        for k, v in sorted(run(args.limit, redo=args.redo).items()):
            print(f"  {k:22} {v}")

    print("chunks:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
