"""Populate book_chunks from stored book text — sections 22 and 27.

    python -m scripts.chunk_pass --limit 500
    python -m scripts.chunk_pass --stats

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

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import OperationalError

from chunking import chunk_text, is_front_matter
from db import SessionLocal
from models import Book, BookChunk, BookText

log = logging.getLogger("chunk_pass")

# Descriptions are chunked into the same table as full text, so their ordinals
# are offset past any realistic chapter count to keep the identity index
# (book_id, user_id, ordinal) collision-free.
DESCRIPTION_ORDINAL_BASE = 1_000_000
# Below this a "description" is a stub — "No description available." and its
# kin — and embedding it produces a confident vector for nothing.
MIN_DESCRIPTION_CHARS = 120


def pending_query(limit: int, redo: bool):
    """Books with stored text but no public chunks yet."""
    already = select(BookChunk.book_id).where(BookChunk.visibility == "public")
    stmt = select(BookText.book_id, BookText.content)
    if not redo:
        stmt = stmt.where(BookText.book_id.notin_(already))
    return stmt.order_by(BookText.book_id).limit(limit)


def chunk_one(session, book_id: int, content: str, origin: str = "text") -> int:
    """Replace this book's public chunks of one origin. Returns how many.

    Scoped to a single `origin` so the two passes do not clobber each other:
    a book can hold Gutenberg prose *and* a provider blurb, and chunking one
    must not delete the other.
    """
    pieces = chunk_text(content)
    if origin == "text":
        # F-40. Descriptions are already prose-filtered upstream by
        # `looks_like_prose`; full text is not, because the reading path wants
        # the front matter kept. Search does not.
        kept = [p for p in pieces if not is_front_matter(p)]
        # Never let the filter empty a book. If everything looks like front
        # matter the detector is wrong about this book, and indexing something
        # beats indexing nothing.
        pieces = kept or pieces
    if not pieces:
        return 0

    # Delete-then-insert rather than upsert: a tuning change alters both the
    # count and the boundaries, so matching old ordinals to new ones is
    # meaningless. Private chunks are untouched — this filters on visibility.
    session.execute(
        delete(BookChunk).where(
            BookChunk.book_id == book_id,
            BookChunk.visibility == "public",
            BookChunk.origin == origin,
        )
    )
    # Ordinals restart per origin, so the unique index needs them not to
    # collide with the other origin's rows. Descriptions are short and few;
    # offsetting them well past any realistic chapter count is simpler and
    # more legible than a composite key.
    offset = DESCRIPTION_ORDINAL_BASE if origin == "description" else 0
    session.add_all(
        BookChunk(
            book_id=book_id,
            user_id=None,
            visibility="public",
            origin=origin,
            ordinal=offset + i,
            content=piece,
            char_count=len(piece),
        )
        for i, piece in enumerate(pieces)
    )
    return len(pieces)


def description_query(limit: int, redo: bool):
    """Enriched books whose description is not yet chunked.

    Section 27 can only retrieve what is indexed, and full text exists for
    Gutenberg alone. Descriptions are what every other enriched book has, so
    indexing them is the difference between semantic search covering ~1,000
    books and covering the enriched catalogue.
    """
    already = select(BookChunk.book_id).where(BookChunk.origin == "description")
    stmt = select(Book.id, Book.description).where(
        Book.description.isnot(None),
        func.length(Book.description) >= MIN_DESCRIPTION_CHARS,
    )
    if not redo:
        stmt = stmt.where(Book.id.notin_(already))
    return stmt.order_by(Book.ratings_count.desc(), Book.id).limit(limit)


def run(limit: int, redo: bool = False, origin: str = "text") -> dict:
    written = books = 0
    empty = 0
    query = pending_query if origin == "text" else description_query

    with SessionLocal() as session:
        rows = session.execute(query(limit, redo)).all()
        log.info(f"{len(rows)} {origin} source(s) queued")

        for book_id, content in rows:
            try:
                n = chunk_one(session, book_id, content, origin=origin)
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

    return {
        "origin": origin,
        "books_chunked": books,
        "chunks_written": written,
        "no_content": empty,
    }


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
        by_origin = dict(
            session.execute(
                select(BookChunk.origin, func.count()).group_by(BookChunk.origin)
            ).all()
        )
        return {
            "book_texts": texts,
            "chunks_by_origin": by_origin or "none",
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
    parser.add_argument(
        "--origin",
        default="text",
        choices=["text", "description"],
        help="chunk stored full text, or provider descriptions",
    )
    parser.add_argument("--stats", action="store_true", help="report only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if not args.stats:
        for k, v in sorted(run(args.limit, redo=args.redo, origin=args.origin).items()):
            print(f"  {k:22} {v}")

    print("chunks:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
