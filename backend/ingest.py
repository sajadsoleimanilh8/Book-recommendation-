"""Load site_ready_books.json into Postgres.

    python -m ingest            # idempotent upsert
    python -m ingest --stats    # report only, no writes

The interesting problem here is identity (F-10). The JSONL mixes two
provenances in one `book_id` field:

    {"book_id": "rOQQUJz68q8C", ...}   Google Books volume id (string)
    {"book_id": 3054, ...}             Goodreads id (integer)

Those namespaces overlap numerically and cannot share a column meaningfully,
which is why the schema keys on (source, external_id) rather than trusting
`book_id` alone. Source is inferred per record below.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

import config
from db import SessionLocal
from models import Book

PLACEHOLDER_DESCRIPTIONS = {"", "no description available", "none", "null"}
PLACEHOLDER_THUMBNAILS = {"/static/images/default-book.jpg"}

BATCH_SIZE = 1000


def infer_source(raw_id: Any) -> str:
    """Google Books volume ids are 12-char alphanumeric strings; Goodreads
    ids are integers. Anything else is recorded as 'unknown' rather than
    guessed, so it can be found later."""
    if isinstance(raw_id, int):
        return "goodreads"
    s = str(raw_id).strip()
    if not s:
        return "unknown"
    if s.isdigit():
        return "goodreads"
    return "google_books"


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
        "source": infer_source(rec.get("book_id")),
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
            stmt = stmt.on_conflict_do_update(
                constraint="uq_books_source_external",
                set_={
                    c: stmt.excluded[c]
                    for c in (
                        "title", "author", "genre", "description", "language",
                        "average_rating", "ratings_count", "page_count",
                        "list_price", "published_year", "thumbnail",
                    )
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
                select(Book.source, func.count()).group_by(Book.source)
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
    args = parser.parse_args()

    if not args.stats:
        result = ingest()
        print("ingest:")
        for k, v in sorted(result.items()):
            print(f"  {k:24} {v}")

    print("database:")
    for k, v in report().items():
        print(f"  {k:24} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
