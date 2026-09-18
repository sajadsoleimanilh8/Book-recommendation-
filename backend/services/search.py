"""Semantic search over book chunks — sections 22 and 27.

The pipeline section 27 asks for:

    query -> embedding -> vector retrieval -> metadata filters -> ranking
          -> grounded results

"Grounded" is the load-bearing word. Every result here is a row that exists in
`book_chunks`, carrying the book it came from and the passage that matched.
Nothing is generated, so there is no path by which this can invent a book —
the failure mode is returning nothing, which is the correct failure.

Authorization
-------------
Section 22: a user's private chunks must never appear in another user's
search. That is enforced in one place, `visible_chunks()`, and every retrieval
path goes through it. The rule stated plainly:

    anonymous  -> public chunks only
    signed in  -> public chunks, plus that user's own private chunks
    never      -> anybody else's private chunks

The anonymous case is written as an explicit `false()` rather than left to
`user_id == None`, which SQLAlchemy renders as `user_id IS NULL` and would
quietly match every public row through the private branch. It is the same
result today and a security hole the moment the branch changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import Select, and_, false, or_, select
from sqlalchemy.orm import Session

from db.models import Book, BookChunk, BookVector

log = logging.getLogger(__name__)

# Below this cosine similarity a result is noise. Returning it would satisfy
# "the search returned something" while telling the reader nothing, and with
# LSA a low-similarity match is usually a shared stop-word artefact.
MIN_SIMILARITY = 0.05
DEFAULT_LIMIT = 10
MAX_LIMIT = 50


@dataclass(frozen=True)
class SearchHit:
    book_id: int
    title: str
    author: str | None
    chunk_id: int
    ordinal: int
    passage: str
    similarity: float
    visibility: str
    origin: str

    def as_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "author": self.author,
            "chunk_id": self.chunk_id,
            "ordinal": self.ordinal,
            "passage": self.passage,
            "similarity": round(self.similarity, 4),
            "visibility": self.visibility,
            # Section 11: a caller must be able to tell public-domain prose
            # from a third-party blurb. They are also different evidence that
            # a book matches — a chapter shows the writing, a blurb describes
            # it — so a reader deserves to know which they are looking at.
            "origin": self.origin,
        }


def visible_chunks(user_id: int | None):
    """The authorization predicate. Every retrieval path must use this.

    Kept as a single expression rather than inlined at call sites so there is
    exactly one place to audit, and so a new endpoint cannot accidentally ship
    without it.
    """
    public = and_(BookChunk.visibility == "public", BookChunk.user_id.is_(None))
    if user_id is None:
        return public
    return or_(public, BookChunk.user_id == user_id)


def _apply_filters(
    stmt: Select,
    *,
    language: str | None,
    genre: str | None,
    min_year: int | None,
    max_year: int | None,
) -> Select:
    """Metadata filters, applied after retrieval and before ranking (§27)."""
    if language:
        stmt = stmt.where(Book.language == language)
    if genre:
        stmt = stmt.where(Book.genre == genre)
    if min_year:
        stmt = stmt.where(Book.published_year >= min_year)
    if max_year:
        stmt = stmt.where(Book.published_year <= max_year)
    return stmt


def search_chunks(
    session: Session,
    query_vector: Sequence[float],
    *,
    user_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    language: str | None = None,
    genre: str | None = None,
    min_year: int | None = None,
    max_year: int | None = None,
    min_similarity: float = MIN_SIMILARITY,
    embedding_model: str | None = None,
) -> list[SearchHit]:
    """Nearest chunks the caller is allowed to see, most similar first.

    `embedding_model` restricts the search to chunks embedded by the same
    model as the query vector. Cosine similarity between two different vector
    spaces is a number, not a measurement: it produces confident-looking
    rankings from noise. Caught in testing, where a query encoded with one
    backend against chunks embedded with another returned an empty list
    instead of the exact-match passage.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))

    # pgvector's `<=>` is cosine *distance*; similarity is 1 - distance. The
    # vectors are unit-length (see embeddings._normalise), so this is exact
    # rather than an approximation.
    distance = BookChunk.embedding.cosine_distance(list(query_vector))

    stmt = (
        select(
            BookChunk.id,
            BookChunk.book_id,
            BookChunk.ordinal,
            BookChunk.content,
            BookChunk.visibility,
            BookChunk.origin,
            Book.title,
            Book.author,
            distance.label("distance"),
        )
        .join(Book, Book.id == BookChunk.book_id)
        .where(visible_chunks(user_id))
        .where(BookChunk.embedding.isnot(None))
    )
    if embedding_model:
        stmt = stmt.where(BookChunk.embedding_model == embedding_model)
    stmt = _apply_filters(
        stmt, language=language, genre=genre, min_year=min_year, max_year=max_year
    )
    stmt = stmt.order_by(distance).limit(limit)

    hits: list[SearchHit] = []
    for row in session.execute(stmt):
        similarity = 1.0 - float(row.distance)
        if similarity < min_similarity:
            continue
        hits.append(
            SearchHit(
                book_id=row.book_id,
                title=row.title,
                author=row.author,
                chunk_id=row.id,
                ordinal=row.ordinal,
                passage=row.content,
                similarity=similarity,
                visibility=row.visibility,
                origin=row.origin,
            )
        )
    return hits


@dataclass(frozen=True)
class BookHit:
    """A book-level semantic match — F-44's `book_vectors`, not a passage.

    Deliberately not `SearchHit`: there is no chunk, no ordinal, no passage
    text, and forcing this into that shape would mean inventing placeholder
    values for fields that do not apply. `has_description` says whether the
    vector saw real description text or only title/author/genre — the same
    "thin vs rich" distinction `book_vectors` itself records, useful for a
    caller (or an LLM grounding on this) to know how much to trust a match.
    """

    book_id: int
    title: str
    author: str | None
    similarity: float
    has_description: bool

    def as_dict(self) -> dict:
        return {
            "book_id": self.book_id,
            "title": self.title,
            "author": self.author,
            "similarity": round(self.similarity, 4),
            "has_description": self.has_description,
        }


def search_books(
    session: Session,
    query_vector: Sequence[float],
    *,
    user_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    min_similarity: float = MIN_SIMILARITY,
    embedding_model: str | None = None,
    **filters,
) -> list[dict]:
    """Nearest books by `book_vectors` — one embedding per book, F-44.

    Replaces the earlier "collapse `search_chunks` to one row per book"
    approach (kept as `search_chunks`/`search_by_passage` for passage-level
    callers). That approach could only ever find the 23.3% of the catalogue
    that has a chunk — a book with no description and no stored text was
    invisible to book-level search regardless of how well it matched,
    silently, since an empty result looks identical to "nothing matched."
    `book_vectors` covers the whole catalogue (100%, F-44) with a uniform
    recipe, so this covers every book, not just the ones with rich text.

    `user_id` is accepted but unused: `book_vectors` has no per-user rows to
    authorize (unlike `book_chunks`, which carries private uploads, §22) —
    every book vector is derived from the public catalogue. Kept in the
    signature so callers do not need to branch between this and
    `search_chunks`.
    """
    limit = max(1, min(int(limit), MAX_LIMIT))

    distance = BookVector.embedding.cosine_distance(list(query_vector))
    stmt = (
        select(
            BookVector.book_id,
            BookVector.has_description,
            Book.title,
            Book.author,
            distance.label("distance"),
        )
        .join(Book, Book.id == BookVector.book_id)
        .where(BookVector.embedding.isnot(None))
    )
    if embedding_model:
        stmt = stmt.where(BookVector.embedding_model == embedding_model)
    stmt = _apply_filters(
        stmt,
        language=filters.get("language"),
        genre=filters.get("genre"),
        min_year=filters.get("min_year"),
        max_year=filters.get("max_year"),
    )
    stmt = stmt.order_by(distance).limit(limit)

    hits: list[dict] = []
    for row in session.execute(stmt):
        similarity = 1.0 - float(row.distance)
        if similarity < min_similarity:
            continue
        hits.append(
            BookHit(
                book_id=row.book_id,
                title=row.title,
                author=row.author,
                similarity=similarity,
                has_description=row.has_description,
            ).as_dict()
        )
    return hits
