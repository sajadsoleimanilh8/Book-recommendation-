"""Retrieval quality measurement — a number to decide backends by.

    python -m scripts.eval_search
    python -m scripts.eval_search --probes 300

Why this exists
---------------
OI-9 asks whether to install a transformer embedding model. That question is
unanswerable without a number: "the results look better" is how a team spends
a week on a change that made retrieval worse. This produces a figure that can
be compared across backends, before and after.

The metric
----------
**Same-book-in-top-10.** Take a chunk, search with it, and ask whether a
*different* chunk from the same book comes back in the top ten. It measures
topical coherence — does the space put passages of one book near each other —
and it needs no labelled data, which is what makes it runnable today.

It is a proxy, and worth being explicit about what it does not measure: it
says nothing about whether "a short philosophical book that makes me question
my life" finds the right book, because nobody has labelled that. Treat it as a
regression guard and a backend comparison, not as a quality score.

Self-retrieval — searching with a chunk's exact text and expecting itself
first — was tried and discarded: it scores 100% on every backend, including
ones that are visibly worse, so it discriminates nothing.

Measured 2026-08-29, LSA backend, 150 probes:

    origin=text          73.3%   (6,000 chunks, 273 books)
    origin=description   56.5%   (3,359 chunks, 2,424 books)

Descriptions score lower and should: a blurb and a chapter of the same book
share far less vocabulary than two chapters do. The gap is a property of the
material, not a defect.

A separate run of the same probe compared dropping the first SVD component —
standard advice for LSA, since every vector shares that direction and it
inflates similarity uniformly. It moved the score from 68.0% to 67.3%, i.e.
nothing. That is the reason this file exists: without a number the change
would have been shipped on the strength of the reasoning alone.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import numpy as np
from sqlalchemy import select

from db import SessionLocal
from models import BookChunk

SAMPLE = 6000
TOP_K = 10
MIN_CHUNKS_PER_BOOK = 3


def load(sample: int, origin: str) -> tuple[np.ndarray, np.ndarray]:
    with SessionLocal() as session:
        rows = session.execute(
            select(BookChunk.book_id, BookChunk.embedding)
            .where(BookChunk.origin == origin, BookChunk.embedding.isnot(None))
            .limit(sample)
        ).all()
    if not rows:
        raise SystemExit(f"no embedded chunks with origin={origin!r}")
    vectors = np.array([r[1] for r in rows], dtype=np.float32)
    books = np.array([r[0] for r in rows])
    return vectors, books


def same_book_in_top_k(
    vectors: np.ndarray, books: np.ndarray, probes: int, k: int = TOP_K
) -> float:
    per_book: dict[int, list[int]] = defaultdict(list)
    for index, book_id in enumerate(books):
        per_book[book_id].append(index)

    # One probe per book, and only from books with enough chunks that a
    # sibling exists to be found. Probing a single-chunk book measures
    # nothing but guarantees a miss.
    candidates = [v[0] for v in per_book.values() if len(v) >= MIN_CHUNKS_PER_BOOK]
    if not candidates:
        raise SystemExit("no book has enough chunks to probe")
    chosen = candidates[:probes]

    hits = 0
    for index in chosen:
        similarities = vectors @ vectors[index]
        similarities[index] = -1.0  # never retrieve the probe itself
        top = np.argsort(-similarities)[:k]
        hits += books[index] in books[top]
    return hits / len(chosen)


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure retrieval quality.")
    parser.add_argument("--probes", type=int, default=150)
    parser.add_argument("--sample", type=int, default=SAMPLE)
    parser.add_argument("--origin", default="text", choices=["text", "description"])
    args = parser.parse_args()

    vectors, books = load(args.sample, args.origin)
    score = same_book_in_top_k(vectors, books, args.probes)

    print(f"  chunks sampled         {len(vectors):,}")
    print(f"  distinct books         {len(set(books.tolist())):,}")
    print(f"  same-book in top {TOP_K}     {score:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
