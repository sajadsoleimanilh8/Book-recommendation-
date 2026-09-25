"""Load site_ready_books.json into Postgres.

    python -m ingest            # idempotent upsert
    python -m ingest --stats    # report only, no writes

The interesting problem here is identity (F-10). The JSONL merges **three**
catalogues into one `book_id` field:

    {"book_id": "rOQQUJz68q8C", ...}   Google Books volume id   13,219
    {"book_id": 3054, ...}             Goodreads id             10,449
    {"book_id": 2149, ...}             Project Gutenberg id      6,307

Two of those use plain integers and their id spaces overlap, so `book_id`
alone is not a key — which is why the schema keys on (source, external_id).
See `infer_source` for how provenance is determined, and F-27 for what went
wrong when it was inferred from the id shape alone.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Iterator

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

import config
from db import SessionLocal
from models import Book, catalogue_only

PLACEHOLDER_DESCRIPTIONS = {"", "no description available", "none", "null"}
PLACEHOLDER_THUMBNAILS = {"/static/images/default-book.jpg"}

BATCH_SIZE = 1000


def infer_source(raw_id: Any, thumbnail: Any = None) -> str:
    """Identify which catalogue a record came from.

    F-27: this originally keyed only on the shape of `book_id` — numeric
    meant Goodreads, alphanumeric meant Google Books. That is wrong, because
    the catalogue merges **three** sources and two of them use plain integer
    ids:

        google_books  13,219   alphanumeric volume ids
        goodreads     10,449   integer ids
        gutenberg      6,307   integer ids

    Goodreads #2149 and Gutenberg #2149 are different books, so treating both
    as 'goodreads' collapsed them onto one key and silently discarded 1,576
    real books at ingest.

    The thumbnail URL is the reliable discriminator: Gutenberg records point
    at gutenberg.org, Google Books at books.google.com, and Goodreads records
    carry the local placeholder cover. Id shape is only the fallback.
    """
    thumb = str(thumbnail or "").lower()
    if "gutenberg.org" in thumb:
        return "gutenberg"
    if "books.google" in thumb:
        return "google_books"

    s = str(raw_id).strip()
    if not s:
        return "unknown"
    # No usable thumbnail: fall back to id shape. Alphanumeric volume ids are
    # Google's; bare integers are Goodreads, whose records are exactly the
    # ones carrying the placeholder cover.
    return "goodreads" if s.isdigit() else "google_books"


def clean_description(value: Any) -> str | None:
    """Return None for placeholders.

    100% of the current catalogue carries the literal string 'No description
    available' (F-15). Storing that as text would make `description IS NULL`
    useless as the Phase 2 backfill query, and would let a placeholder be
    embedded as if it were content.
    """
    if value is None:
        return None
    s = str(value).strip()
    return None if s.lower() in PLACEHOLDER_DESCRIPTIONS else s


def clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    s = str(value).strip()
    return s or default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_records(path) -> Iterator[dict]:
    """The file is JSONL despite the .json extension. Tolerate a JSON array
    too, in case it is ever regenerated in that shape."""
    with open(path, "r", encoding="utf-8") as f:
        first = f.read(1)
        f.seek(0)
        if first == "[":
            yield from json.load(f)
            return
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def to_row(rec: dict) -> dict | None:
    external_id = clean_str(rec.get("book_id"))
    title = clean_str(rec.get("title"))
    if not external_id or not title:
        return None

    year = to_int(rec.get("published_year"))
    thumbnail = clean_str(rec.get("thumbnail")) or None
    if thumbnail in PLACEHOLDER_THUMBNAILS:
        thumbnail = None

    language = clean_str(rec.get("language")) or None

    return {
        "source": infer_source(rec.get("book_id"), rec.get("thumbnail")),
        "external_id": external_id,
        "title": title,
        "author": clean_str(rec.get("author"), "Unknown"),
        "genre": clean_str(rec.get("genre"), "Unknown"),
        "description": clean_description(rec.get("description")),
        "language": language.lower()[:10] if language else None,
        "average_rating": to_float(rec.get("average_rating")),
        "ratings_count": to_int(rec.get("ratings_count")),
        "page_count": to_int(rec.get("page_count")),
        "list_price": to_float(rec.get("list_price")),
        "published_year": year if year > 0 else None,
        "thumbnail": thumbnail,
    }


def prune(path=None) -> dict:
    """Delete book rows that the catalogue file no longer describes.

    Needed because an upsert only converges the rows it touches. When F-27's
    source correction moved 6,307 Gutenberg books from `goodreads` to
    `gutenberg`, the rows they used to occupy were left behind holding stale
    data under a key nothing maps to any more.

    Deliberately a separate, explicit step rather than part of `ingest`:
    it deletes, and cascades to any user state referencing those books.
    """
    path = path or config.CATALOGUE_PATH
    valid: set[tuple[str, str]] = set()
    for rec in read_records(path):
        row = to_row(rec)
        if row is not None:
            valid.add((row["source"], row["external_id"]))

    removed = []
    with SessionLocal() as session:
        # Phase 4: catalogue only, and this is the one that would have lost
        # data rather than leaked it. This loop deletes every book whose
        # (source, external_id) is absent from the catalogue file — and an
        # uploaded book is never in that file, so every upload on the
        # instance would be destroyed by the next `python -m ingest`, which
        # the README tells people to re-run freely because it is idempotent.
        for book in session.scalars(catalogue_only(select(Book))):
            if (book.source, book.external_id) not in valid:
                removed.append((book.id, book.source, book.external_id, book.title))
                session.delete(book)
        session.commit()

    return {"removed": len(removed), "examples": removed[:5]}


def ingest(path=None) -> dict:
    path = path or config.CATALOGUE_PATH
    if not path.exists():
        raise FileNotFoundError(f"Catalogue not found: {path}")

    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    stats = Counter()

    for rec in read_records(path):
        stats["read"] += 1
        row = to_row(rec)
        if row is None:
            stats["skipped_invalid"] += 1
            continue
        key = (row["source"], row["external_id"])
        if key in seen:
            # Within-file duplicates would make the batch upsert raise
            # "ON CONFLICT DO UPDATE command cannot affect row a second
            # time". Last one wins, matching upsert semantics.
            stats["duplicates_in_file"] += 1
            rows = [r for r in rows if (r["source"], r["external_id"]) != key]
        seen.add(key)
        rows.append(row)
        stats[f"source_{row['source']}"] += 1
        if row["description"]:
            stats["with_description"] += 1

    written = 0
    with SessionLocal() as session:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            stmt = insert(Book).values(batch)
            # Idempotent: re-running refreshes metadata rather than
            # duplicating or failing. created_at is preserved.
            # Enrichment belongs to a *book*, not to a row. If re-ingesting
            # changes which book this row holds — which F-27's source
            # correction does for mis-keyed records — the stored description
            # and ISBNs describe the previous occupant and must be discarded,
            # or one book's blurb ends up attached to another.
            #
            # When the title is unchanged the row still holds the same book,
            # so enrichment is preserved and the Google Books quota already
            # spent on it is not thrown away.
            same_book = Book.title == stmt.excluded.title
            keep_if_same = lambda col: case(  # noqa: E731
                (same_book, getattr(Book, col)), else_=None
            )

            stmt = stmt.on_conflict_do_update(
                constraint="uq_books_source_external",
                set_={
                    **{
                        c: stmt.excluded[c]
                        for c in (
                            "title", "author", "genre", "language",
                            "average_rating", "ratings_count", "page_count",
                            "list_price", "published_year", "thumbnail",
                        )
                    },
                    "description": keep_if_same("description"),
                    "isbn_10": keep_if_same("isbn_10"),
                    "isbn_13": keep_if_same("isbn_13"),
                    "enrichment_source": keep_if_same("enrichment_source"),
                    "enriched_at": keep_if_same("enriched_at"),
                    "enrichment_status": case(
                        (same_book, Book.enrichment_status), else_="pending"
                    ),
                },
            )
            session.execute(stmt)
            written += len(batch)
        session.commit()

    stats["written"] = written
    return dict(stats)


def report() -> dict:
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(Book)) or 0
        if not total:
            return {"books": 0}
        with_desc = session.scalar(
            select(func.count()).select_from(Book).where(Book.description.isnot(None))
        )
        by_source = dict(
            session.execute(
                catalogue_only(select(Book.source, func.count())).group_by(Book.source)
            ).all()
        )
        by_lang = dict(
            session.execute(
                select(Book.language, func.count())
                .group_by(Book.language)
                .order_by(func.count().desc())
                .limit(6)
            ).all()
        )
        english = session.scalar(
            select(func.count()).select_from(Book).where(Book.language == "en")
        )
        return {
            "books": total,
            "with_description": with_desc,
            "description_coverage": f"{with_desc / total:.1%}",
            "by_source": by_source,
            "by_language": by_lang,
            "english": english,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest the book catalogue.")
    parser.add_argument("--stats", action="store_true", help="report only, no writes")
    parser.add_argument(
        "--prune",
        action="store_true",
        help="also delete rows the catalogue file no longer describes",
    )
    args = parser.parse_args()

    if not args.stats:
        result = ingest()
        print("ingest:")
        for k, v in sorted(result.items()):
            print(f"  {k:24} {v}")

        if args.prune:
            pruned = prune()
            print("prune:")
            print(f"  removed                  {pruned['removed']}")
            for row in pruned["examples"]:
                print(f"    id={row[0]} ({row[1]}/{row[2]}) {row[3][:44]!r}")

    print("database:")
    for k, v in report().items():
        print(f"  {k:24} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
