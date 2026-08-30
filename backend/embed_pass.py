"""Fill book_chunks.embedding — section 27.

    python -m embed_pass --fit           # fit the LSA model on the corpus
    python -m embed_pass --limit 20000   # embed unembedded chunks
    python -m embed_pass --stats

Two steps, because they fail differently. Fitting reads a corpus sample and
produces one artefact; embedding is a long write-heavy loop over every chunk.
Folding them together would mean a database hiccup at chunk 19,000 costs the
fit as well.

`embedding_model` is written next to every vector. Without it a backend change
silently mixes incompatible vector spaces in one column, and retrieval quality
degrades in a way that looks like bad ranking rather than a bug.
"""

from __future__ import annotations

import argparse
import logging
import sys

from sqlalchemy import bindparam, or_, func, select
from sqlalchemy.exc import OperationalError

from db import SessionLocal
from embeddings import get_backend
from models import EMBEDDING_DIM, BookChunk

log = logging.getLogger("embed_pass")

# How many chunks to sample when fitting. The whole corpus is not needed to
# learn the term structure of English prose, and a bounded sample keeps the
# fit to seconds on a machine already running someone else's training job.
FIT_SAMPLE = 20_000
BATCH = 256


def fit(dim: int = EMBEDDING_DIM, sample: int = FIT_SAMPLE) -> dict:
    backend = get_backend("lsa", dim=dim)

    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(BookChunk)) or 0
        if total < 2:
            raise SystemExit("no chunks to fit on — run `python -m chunk_pass` first")

        # Ordered by id rather than random: a repeatable fit is worth more
        # than a marginally better sample, because an unreproducible model is
        # not debuggable when retrieval looks wrong.
        corpus = list(
            session.scalars(select(BookChunk.content).order_by(BookChunk.id).limit(sample))
        )

    backend.fit(corpus)
    path = backend.save()
    return {"documents": len(corpus), "corpus_available": total, "saved_to": str(path)}


def run(limit: int, redo: bool = False, backend_name: str | None = None) -> dict:
    backend = get_backend(backend_name, dim=EMBEDDING_DIM)
    if hasattr(backend, "load") and backend.name == "lsa":
        backend.load()

    embedded = failed = 0

    with SessionLocal() as session:
        stmt = select(BookChunk.id, BookChunk.content)
        if not redo:
            # F-39. Selecting only NULL vectors was not enough. After a backend
            # change the existing rows keep their old vectors while new ones get
            # the new model, and cosine similarity between two different vector
            # spaces is meaningless — so search degrades silently, with every
            # row individually "embedded" and the corpus as a whole incoherent.
            #
            # `embedding_model` recorded the mix but nothing acted on it. Now a
            # row whose vector came from a different backend is re-embedded like
            # a missing one, which makes switching backends self-healing and a
            # mixed space unreachable without --redo.
            stmt = stmt.where(
                or_(
                    BookChunk.embedding.is_(None),
                    BookChunk.embedding_model.is_(None),
                    BookChunk.embedding_model != backend.name,
                )
            )
        rows = session.execute(stmt.order_by(BookChunk.id).limit(limit)).all()
        log.info(f"{len(rows)} chunk(s) queued (backend={backend.name})")

        for start in range(0, len(rows), BATCH):
            batch = rows[start : start + BATCH]
            try:
                vectors = backend.encode([content for _, content in batch])
                session.execute(
                    BookChunk.__table__.update()
                    .where(BookChunk.id == bindparam("chunk_id"))
                    .values(embedding=bindparam("vec"), embedding_model=backend.name),
                    [
                        {"chunk_id": cid, "vec": vector.tolist()}
                        for (cid, _), vector in zip(batch, vectors)
                    ],
                )
                session.commit()
                embedded += len(batch)
            except OperationalError as exc:
                session.rollback()
                log.error(f"database unreachable after {embedded} chunk(s): {exc}")
                break
            except Exception as exc:
                session.rollback()
                failed += len(batch)
                log.warning(f"batch at {start}: {type(exc).__name__} {exc}")

            if (start // BATCH) % 10 == 0:
                log.info(f"  {embedded}/{len(rows)} embedded")

    return {"embedded": embedded, "failed": failed, "backend": backend.name}


def report() -> dict:
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(BookChunk)) or 0
        done = session.scalar(
            select(func.count()).select_from(BookChunk).where(BookChunk.embedding.isnot(None))
        ) or 0
        models = session.execute(
            select(BookChunk.embedding_model, func.count())
            .where(BookChunk.embedding_model.isnot(None))
            .group_by(BookChunk.embedding_model)
        ).all()
        return {
            "chunks_total": total,
            "chunks_embedded": done,
            "coverage": f"{(done / total if total else 0):.1%}",
            "by_model": {m: n for m, n in models} or "none",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed book chunks.")
    parser.add_argument("--fit", action="store_true", help="fit and save the LSA model")
    parser.add_argument("--limit", type=int, default=50_000)
    parser.add_argument("--redo", action="store_true", help="re-embed everything")
    parser.add_argument("--backend", default=None, help="lsa | hashing | minilm")
    parser.add_argument("--stats", action="store_true", help="report only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if args.fit:
        for k, v in fit().items():
            print(f"  {k:22} {v}")
    elif not args.stats:
        for k, v in run(args.limit, redo=args.redo, backend_name=args.backend).items():
            print(f"  {k:22} {v}")

    print("embeddings:")
    for k, v in report().items():
        print(f"  {k:22} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
