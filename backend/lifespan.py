"""Application startup — catalogue load, ML fit, index build, comment rehydrate.

Lifted verbatim from `main.py`'s startup() and its three helpers (fit_ml,
_build_pk_index, _rehydrate_comments) in the Phase D restructure, with
one transformation applied throughout, the same one D7's
_get_search_encoder needed:

  every `global BOOKS, RECOMMENDER, ...` plus bare assignment becomes a
  `main.BOOKS = ...` / `main.RECOMMENDER = ...` attribute write.

`global` only rebinds names in the function's own module. These
functions used it to rebind main.py's module state — BOOKS, BOOK_BY_ID,
BOOK_IDX_BY_PK, RECOMMENDER, QUESTIONER, DF, DATA_SOURCE,
CONTENT_VECTOR_REPORT — which conftest.py and four test modules read
back as main.RECOMMENDER, main.QUESTIONER etc. AFTER startup() has run
(RESTRUCTURE-NOTES B-4, 5.2). Moved here unchanged, that `global` would
have rebound names in `lifespan`'s namespace and left main's stuck at
their initial None / [] forever. The declarations themselves stay in
main.py — this module only writes them, through the imported module
object.

main.py registers startup() on the app with the same @app.on_event
hook it always used; RESTRUCTURE-PROMPT step 15 says keep the existing
event style, don't migrate to the lifespan context manager here.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import main
import store
from api.search import _get_search_encoder
from db import SessionLocal
from engine import DataLoader, QuestionerEngine, Recommender
from services.catalogue import (
    DATA_FILE,
    DATA_FILE_CANDIDATES,
    _books_to_df,
    load_books_raw,
    overlay_languages,
)

log = logging.getLogger(__name__)


def fit_ml(books: List[Dict[str, Any]]):
    if not books:
        return None, None, None

    df = _books_to_df(books)
    recommender = Recommender(df)

    # F-44: content similarity on MiniLM book vectors when they are all
    # present. Loaded here rather than inside the engine so a database outage
    # degrades one feature instead of stopping the app from booting.
    content_vectors = None
    try:
        with SessionLocal() as session:
            content_vectors, main.CONTENT_VECTOR_REPORT = store.load_content_vectors(
                session, books
            )
    except Exception as exc:
        main.CONTENT_VECTOR_REPORT = {"error": f"{type(exc).__name__}: {exc}"}
        log.warning(f"content vectors unavailable: {main.CONTENT_VECTOR_REPORT['error']}")

    if main.CONTENT_VECTOR_REPORT.get("error"):
        log.warning(f"content vectors: {main.CONTENT_VECTOR_REPORT['error']}")

    # F-46: the same encoder that produced the book vectors, so a stated
    # preference is compared in the space the documents live in. None when
    # search is unconfigured, which leaves the old ordering behaviour.
    encoder = _get_search_encoder()
    query_encoder = None
    if encoder is not None and content_vectors is not None:
        if getattr(encoder, "name", None) == main.CONTENT_VECTOR_REPORT.get("models", [None])[0]:
            query_encoder = encoder.encode
        else:
            log.warning(
                f"query encoder {getattr(encoder, 'name', None)!r} does not "
                f"match book vector model {main.CONTENT_VECTOR_REPORT.get('models')} "
                "— profile queries stay on the positional fallback"
            )

    recommender.fit(content_vectors, query_encoder=query_encoder)

    for i, b in enumerate(books):
        if i < len(recommender.df):
            b["cluster"] = int(recommender.df.loc[i, "cluster"])
        else:
            b["cluster"] = -1

    questioner = QuestionerEngine(recommender)
    return recommender, recommender.df, questioner


def startup():

    log.info(f"Loading books from {DATA_FILE}…")
    # F-20 (partial): the limit=5000 cap silently truncated the catalogue to
    # 5000 of 29975 books. Configurable now, and unbounded by default.
    main.BOOKS = load_books_raw(limit=main.BOOK_LOAD_LIMIT)
    main.DATA_SOURCE = "json" if DATA_FILE.exists() else ("csv_fallback" if main.BOOKS else "none")

    if not main.BOOKS:
        # F-03: this path used to be reached silently and reported as
        # "csv_fallback" by /health. It is now loud, and /health says
        # "synthetic" so nobody demos fabricated data by accident.
        log.error(
            "NO REAL BOOK DATA FOUND — falling back to SYNTHETIC data. "
            f"Expected the catalogue at {DATA_FILE_CANDIDATES[0]}. "
            "Recommendations will be meaningless."
        )
        main.DATA_SOURCE = "synthetic"
        try:
            raw_df = DataLoader.load([])
            main.BOOKS = raw_df.to_dict("records")
            for i, b in enumerate(main.BOOKS):
                b["id"] = i + 1
        except Exception as e:
            log.error(f"Failed to load synthetic data: {e}")
            main.BOOKS = []
            main.DATA_SOURCE = "none"

    main.BOOK_BY_ID = {b["id"]: b for b in main.BOOKS}
    log.info(f"Loaded {len(main.BOOKS)} books.")

    # F-48 and F-26. Both must run before fit_ml, because the recommender
    # builds its DataFrame from main.BOOKS and anything attached afterwards
    # never reaches ranking. Only real catalogue data — the synthetic fallback
    # has no database counterpart.
    #   F-48: replace seed language labels with the database's; the seed file
    #         labels every Gutenberg book Italian.
    #   F-26: attach reading-depth evidence, the relevance target's input.
    # Independent of each other; language first because it corrects the
    # catalogue itself.
    if main.DATA_SOURCE in ("json", "csv_fallback"):
        _overlay_db_languages()
        _attach_reading_depth()

    if main.BOOKS:
        log.info("Fitting ML engine…")
        try:
            main.RECOMMENDER, main.DF, main.QUESTIONER = fit_ml(main.BOOKS)
            if main.RECOMMENDER:
                log.info("ML engine ready — all features active.")
        except Exception as e:
            log.error(f"ML fit failed: {e}")
            main.RECOMMENDER = None
            main.QUESTIONER = None
    else:
        log.warning("Skipping ML fit due to empty book list.")

    if main.RECOMMENDER is not None:
        _build_pk_index()
        _rehydrate_comments()

    # Outside the block above on purpose: semantic search reads from Postgres
    # and does not depend on the recommender, so a failed ML fit must not also
    # take search down. Failure here is recorded, never fatal.
    _get_search_encoder()


def _attach_reading_depth() -> None:
    """Attach per-book reading depth to the catalogue — F-26's target.

    Degrades rather than cascades (section 12): with Postgres down the app
    boots on the popularity prior alone, which is exactly the behaviour with
    zero readers, and says so.
    """
    try:
        from db import SessionLocal
        from services.reading_depth import load_reading_depth

        with SessionLocal() as session:
            depth = load_reading_depth(session)
    except Exception as exc:
        log.warning(
            f"reading depth unavailable: {type(exc).__name__}: {exc}. "
            "Ranking falls back to the popularity prior."
        )
        main.READING_DEPTH_REPORT = {"error": f"{type(exc).__name__}: {exc}"}
        return

    matched = readers = 0
    for book in main.BOOKS:
        key = (str(book.get("source") or ""), str(book.get("book_id") or "").strip())
        found = depth.get(key)
        if found is None:
            continue
        book["reading_depth"], book["reading_depth_n"] = found
        matched += 1
        readers += found[1]

    main.READING_DEPTH_REPORT = {
        "books_with_readers": matched,
        "total_starts": readers,
        "books_measured": len(depth),
    }
    log.info(f"reading depth: {main.READING_DEPTH_REPORT}")


def _overlay_db_languages() -> None:
    """Replace seed language labels with the database's — F-48.

    Failure degrades rather than cascades (section 12): with Postgres down
    the app still boots, on the seed labels, and says so. The one thing it
    must not do is fail silently, because the seed labels file every
    Gutenberg book as Italian and that is invisible downstream.
    """
    try:
        from sqlalchemy import select

        from db import SessionLocal
        from models import Book

        with SessionLocal() as session:
            rows = session.execute(
                select(Book.source, Book.external_id, Book.language).where(
                    Book.language.isnot(None), Book.external_id.isnot(None)
                )
            ).all()
    except Exception as exc:
        log.warning(
            f"language overlay skipped, database unavailable: "
            f"{type(exc).__name__}: {exc}. Running on seed labels, which "
            "mislabel Gutenberg (F-48)."
        )
        main.LANGUAGE_OVERLAY = {"error": f"{type(exc).__name__}: {exc}"}
        return

    mapping = {(str(src), str(ext).strip()): lang for src, ext, lang in rows}
    counts = overlay_languages(main.BOOKS, mapping)
    main.LANGUAGE_OVERLAY = counts
    log.info(f"language overlay: {counts}")


def _build_pk_index() -> None:
    """Map books.id -> DataFrame row index, once, at startup.

    Doing this per-request would be 28k lookups; doing it by title would
    collide on the 1,576 duplicate records F-25 found.
    """
    main.BOOK_IDX_BY_PK = {}
    try:
        with SessionLocal() as session:
            for book in main.BOOKS:
                pk = store.resolve_book_pk(session, book)
                if pk is not None:
                    # First occurrence wins, matching the ingest's
                    # de-duplication so both sides agree on which row is
                    # canonical.
                    main.BOOK_IDX_BY_PK.setdefault(pk, store.df_index_for(book))
        log.info(f"Mapped {len(main.BOOK_IDX_BY_PK)} books to persistent ids.")
    except Exception as e:
        log.error(f"Could not build the book id index: {e}")


def _rehydrate_comments() -> None:
    """F-12: restore persisted comments into the ranking-facing cache.

    Before this, a restart erased every comment and reset comment_score to
    0.0. Comments are replayed in id order so the cache matches the order
    GET returns, which is what keeps delete-by-position aligned.

    Note this legitimately changes comment_score at boot, which is a ranking
    input. It cannot currently change rankings, because comment_score has
    zero LTR importance (F-26) — but that is a bug, not a guarantee. When
    Phase 3 fixes F-26 this becomes a real behavioural difference between a
    cold and a warm start, and will need the fitted model to account for it.
    """
    if main.RECOMMENDER is None or getattr(main.RECOMMENDER, "comment", None) is None:
        return
    try:
        with SessionLocal() as session:
            grouped = store.all_comments_by_book(session)
        restored = 0
        for book_pk, rows in grouped.items():
            book_idx = main.BOOK_IDX_BY_PK.get(book_pk)
            if book_idx is None:
                continue
            for row in rows:
                main.RECOMMENDER.comment.add(
                    book_idx=book_idx,
                    user_id=str(row.user_id),
                    text=row.text,
                    rating=row.rating,
                    profile=None,
                )
                restored += 1
        if restored:
            log.info(f"Rehydrated {restored} comment(s) from Postgres.")
    except Exception as e:
        log.error(f"Comment rehydration failed: {e}")
