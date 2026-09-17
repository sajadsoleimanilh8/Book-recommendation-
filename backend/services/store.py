"""Persistence for user state — closes F-12.

Comments, reading progress and reminders lived in Python dicts on the
`Recommender` instance. Every restart wiped them, and two uvicorn workers
meant two divergent universes.

The tricky part is book identity, and the resolution is deliberately narrow:

    the ML engine still loads books from the JSONL, exactly as before.

`Recommender` is built from a DataFrame whose row order comes from that file,
and the ranking path depends on that order. Repointing it at Postgres would
reshuffle rows and change every recommendation — a ranking change inside a
persistence PR, which is precisely what the golden baselines exist to
prevent. So instead:

    in-memory book  --(external_id)-->  books.id  --> user state
    positional id                       stable PK

`resolve_book_pk` does that lookup once per request and caches it.

CommentEngine keeps its in-memory store as the *ranking-facing cache* — it is
what feeds `comment_score`. Postgres is the source of truth, and the cache is
rehydrated from it at startup. Write-through, not write-behind: a comment is
committed before the response returns.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ingest import infer_source
from db.models import Book, BookVector, Comment, ReadingProgress, Reminder

log = logging.getLogger(__name__)

# external_id -> books.id. The catalogue is immutable at runtime, so this
# never needs invalidating within a process.
_pk_cache: dict[tuple[str, str], int | None] = {}


def resolve_book_pk(session: Session, book: dict[str, Any]) -> int | None:
    """Map an in-memory book (positional id) to its durable books.id.

    Returns None when the book is not in the database — possible because the
    ingest de-duplicates 1,576 records (F-25) that the JSONL loader still
    presents as distinct rows.
    """
    external_id = str(book.get("book_id") or "").strip()
    if not external_id:
        return None

    # F-27: the thumbnail disambiguates Goodreads from Gutenberg, which
    # share an integer id space. Passing it is required for correctness.
    key = (infer_source(book.get("book_id"), book.get("thumbnail")), external_id)
    if key in _pk_cache:
        return _pk_cache[key]

    pk = session.scalar(
        select(Book.id).where(Book.source == key[0], Book.external_id == key[1])
    )
    _pk_cache[key] = pk
    return pk


def df_index_for(book: dict[str, Any]) -> int:
    """DataFrame row index for an in-memory book.

    `_books_to_df` preserves list order and `Recommender.__init__` calls
    `reset_index(drop=True)`, so books[i] is DataFrame row i, and
    books[i]["id"] == i + 1.

    This replaces the previous `DF[DF["title"] == b["title"]].index[0]`
    lookup, which silently attached a comment to the *first* book sharing a
    title. With 1,576 duplicate-id records in the catalogue (F-25) and many
    more shared titles, that collided routinely.
    """
    return int(book["id"]) - 1


# --------------------------------------------------------------------------
# Comments
# --------------------------------------------------------------------------


def save_comment(
    session: Session,
    *,
    user_id: int,
    book_pk: int,
    text: str,
    rating: int | None,
    sentiment: str | None,
    keywords: list[str] | None,
) -> Comment:
    comment = Comment(
        user_id=user_id,
        book_id=book_pk,
        text=text,
        rating=rating,
        sentiment=sentiment,
        keywords=keywords,
    )
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return comment


def delete_comment(session: Session, *, comment_id: int, user_id: int) -> bool:
    """Delete only if owned. Returns False when the row is absent or owned by
    someone else — the caller cannot distinguish, which is intentional."""
    result = session.execute(
        delete(Comment).where(Comment.id == comment_id, Comment.user_id == user_id)
    )
    session.commit()
    return result.rowcount > 0


def comments_for_book(session: Session, book_pk: int) -> list[Comment]:
    return list(
        session.scalars(
            select(Comment).where(Comment.book_id == book_pk).order_by(Comment.id)
        )
    )


def all_comments_by_book(session: Session) -> dict[int, list[Comment]]:
    """Every comment, grouped by books.id — used to rehydrate the cache."""
    grouped: dict[int, list[Comment]] = {}
    for c in session.scalars(select(Comment).order_by(Comment.id)):
        grouped.setdefault(c.book_id, []).append(c)
    return grouped


# --------------------------------------------------------------------------
# Reading progress
# --------------------------------------------------------------------------


def save_progress(
    session: Session, *, user_id: int, book_pk: int, progress: float, page: int = 0
) -> None:
    progress = max(0.0, min(1.0, float(progress)))
    stmt = insert(ReadingProgress).values(
        user_id=user_id, book_id=book_pk, progress=progress, page=page
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[ReadingProgress.user_id, ReadingProgress.book_id],
        set_={"progress": stmt.excluded.progress, "page": stmt.excluded.page},
    )
    session.execute(stmt)
    session.commit()


def progress_for_user(session: Session, user_id: int) -> list[ReadingProgress]:
    # Most-recently-read first — the "continue reading" UI (OI-6 follow
    # through) shows this list as-is, and a reader expects their most recent
    # book at the top, not database insertion order.
    return list(
        session.scalars(
            select(ReadingProgress)
            .where(ReadingProgress.user_id == user_id)
            .order_by(ReadingProgress.updated_at.desc())
        )
    )


# --------------------------------------------------------------------------
# Reminders
# --------------------------------------------------------------------------


def set_reminder(
    session: Session, *, user_id: int, book_pk: int, enabled: bool
) -> None:
    stmt = insert(Reminder).values(user_id=user_id, book_id=book_pk, enabled=enabled)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_reminder_user_book",
        set_={"enabled": stmt.excluded.enabled},
    )
    session.execute(stmt)
    session.commit()


def reminders_for_user(session: Session, user_id: int) -> list[Reminder]:
    return list(session.scalars(select(Reminder).where(Reminder.user_id == user_id)))


def load_content_vectors(
    session: Session, books: list[dict[str, Any]]
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """MiniLM book vectors aligned to DataFrame row order — F-44.

    Returns `(vectors, report)`, or `(None, report)` when they cannot be used.

    **All or nothing, deliberately.** A missing vector would have to be filled
    with zeros, and a zero vector has cosine similarity 0 with everything — so
    that book would never surface as anyone's neighbour. It would not error;
    it would simply vanish from content-based recommendation, which is the
    exact failure F-44 exists to prevent, reintroduced at load time. If even
    one book is missing, the caller falls back to TF-IDF for the whole
    catalogue and says so.
    """
    report: dict[str, Any] = {"requested": len(books), "found": 0, "missing": 0}
    if not books:
        return None, report

    rows = session.execute(
        select(BookVector.book_id, BookVector.embedding, BookVector.embedding_model)
        .where(BookVector.embedding.isnot(None))
    ).all()
    by_pk = {pk: vec for pk, vec, _ in rows}
    models = {m for _, _, m in rows}
    report["models"] = sorted(m for m in models if m)

    if len(report["models"]) > 1:
        # Two vector spaces in one column: cosine between them is a number,
        # not a measurement (F-39).
        report["error"] = f"multiple embedding models present: {report['models']}"
        return None, report

    vectors, missing = [], 0
    for book in books:
        pk = resolve_book_pk(session, book)
        vec = by_pk.get(pk) if pk is not None else None
        if vec is None:
            missing += 1
            if missing <= 3:
                log.warning(f"no content vector for {book.get('title', '?')[:50]!r}")
        else:
            vectors.append(vec)

    report["found"] = len(vectors)
    report["missing"] = missing
    if missing:
        report["error"] = (
            f"{missing} of {len(books)} books have no vector — falling back to "
            "TF-IDF rather than letting those books silently drop out of "
            "content ranking"
        )
        return None, report

    return np.asarray(vectors, dtype=np.float32), report
