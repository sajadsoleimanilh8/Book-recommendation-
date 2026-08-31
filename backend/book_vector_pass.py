"""One embedding per book, for content similarity — F-44.

    python -m book_vector_pass --limit 40000
    python -m book_vector_pass --stats

Why a book-level vector and not the chunks we already have
----------------------------------------------------------
`content_s` currently comes from TF-IDF + SVD over the catalogue text. The
obvious upgrade is the MiniLM vectors already in `book_chunks` — measured at
84.0% same-book@10 against LSA's 50.7%. But chunks only exist where there is a
description or Gutenberg text:

    books with a chunk     6,989   (23.3%)
    books with NO chunk   22,986   (76.7%)

Content similarity carries weight 0.28 in the final blend and feeds the LTR
component's 0.32, so building from chunks alone would silently drop three
quarters of the catalogue out of content ranking. Nothing errors. The
rankings just quietly get worse, in a way no current test asserts against.

The recipe is uniform, which is the point
-----------------------------------------
Every book: `title. author. genre. description`, with the description simply
absent when there is none. The tempting alternative — mean-of-chunks where
chunks exist, metadata elsewhere — puts two populations with different
character into one vector space. That is F-39 wearing a different hat:
comparable vectors need comparable *construction*, not merely the same model.

`has_description` records which books got the richer text, so a thin vector
can be told apart later without re-deriving it (section 26 explanations will
want this, and it is expensive to backfill).
"""

from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from db import SessionLocal
from embeddings import get_backend
from models import Book, BookVector

log = logging.getLogger("book_vector_pass")

BATCH = 512

# Long descriptions add tokens past what MiniLM attends to (512 tokens) while
# making the batch slower. The opening is the part that characterises a book.
MAX_DESCRIPTION_CHARS = 1200


def build_text(book: Book) -> tuple[str, bool, int]:
    """The uniform recipe. Returns (text, had_description, chars)."""
    parts = [
        str(book.title or "").strip(),
        str(book.author or "").strip(),
        str(book.genre or "").strip(),
    ]
    description = (book.description or "").strip()
    if description:
        parts.append(description[:MAX_DESCRIPTION_CHARS])
    # Periods rather than spaces: MiniLM was trained on sentences, and
    # "Dune. Frank Herbert. Science Fiction." reads as one to it, where
    # "Dune Frank Herbert Science Fiction" reads as a noun pile.
    text = ". ".join(p for p in parts if p)
    return text, bool(description), len(text)


def pending_query(limit: int, redo: bool, model_name: str):
    stmt = select(Book)
    if not redo:
        # Same rule as embed_pass after F-39: a vector from another backend is
        # as stale as a missing one, so a model switch is self-healing.
        stale = select(BookVector.book_id).where(
            BookVector.embedding.isnot(None),
            BookVector.embedding_model == model_name,
        )
        stmt = stmt.where(Book.id.notin_(stale))
    return stmt.order_by(Book.id).limit(limit)


def run(limit: int, redo: bool = False, backend_name: str | None = None) -> dict:
    backend = get_backend(backend_name)
    if hasattr(backend, "load") and backend.name == "lsa":
        backend.load()

    written = skipped = 0
    with SessionLocal() as session:
        books = list(session.scalars(pending_query(limit, redo, backend.name)))
        log.info(f"{len(books)} book(s) queued (backend={backend.name})")

        for start in range(0, len(books), BATCH):
            batch = books[start : start + BATCH]
            rows = []
            for book in batch:
                text, had_description, chars = build_text(book)
                if not text:
                    # A book with no title, author or genre has nothing to
                    # embed. Writing a zero vector would make it spuriously
                    # similar to every other empty one.
                    skipped += 1
                    continue
                rows.append((book.id, text, had_description, chars))

            if not rows:
                continue

            vectors = backend.encode([r[1] for r in rows])
            for (book_id, _, had_description, chars), vector in zip(rows, vectors):
                stmt = insert(BookVector).values(
                    book_id=book_id,
                    embedding=np.asarray(vector, dtype=np.float32).tolist(),
                    embedding_model=backend.name,
                    has_description=had_description,
                    source_chars=chars,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=[BookVector.book_id],
                    set_={
                        "embedding": stmt.excluded.embedding,
                        "embedding_model": stmt.excluded.embedding_model,
                        "has_description": stmt.excluded.has_description,
                        "source_chars": stmt.excluded.source_chars,
                    },
                )
                session.execute(stmt)

            session.commit()
            written += len(rows)
            log.info(f"  {written}/{len(books)} written")

    return {"written": written, "skipped_empty": skipped, "backend": backend.name}


def report() -> dict:
    with SessionLocal() as session:
        books = session.scalar(select(func.count()).select_from(Book)) or 0
        vectors = session.scalar(
            select(func.count()).select_from(BookVector).where(BookVector.embedding.isnot(None))
        ) or 0
        rich = session.scalar(
            select(func.count()).select_from(BookVector).where(BookVector.has_description.is_(True))
        ) or 0
        models = session.execute(
            select(BookVector.embedding_model, func.count()).group_by(BookVector.embedding_model)
        ).all()
        return {
            "books": books,
            "with_vector": vectors,
            "coverage": f"{(vectors / books if books else 0):.1%}",
            "with_description": rich,
            "metadata_only": vectors - rich,
            "by_model": {m: n for m, n in models} or "none",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one embedding per book.")
    parser.add_argument("--limit", type=int, default=40000)
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    if not args.stats:
        for k, v in run(args.limit, redo=args.redo, backend_name=args.backend).items():
            print(f"  {k:22} {v}")
    print("book_vectors:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
