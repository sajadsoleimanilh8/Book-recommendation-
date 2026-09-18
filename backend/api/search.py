"""Search — GET /search, /api/search, /api/search/semantic.

Extracted verbatim from `main.py` in the Phase D restructure, with the
now-established transformation: bare BOOKS and error_response become
main.<name>, since they are main.py's live state / a function only
reachable through main at call time (RESTRUCTURE-NOTES B-4, 5.2).

_SEARCH_ENCODER and _SEARCH_ENCODER_ERROR stay declared in main.py —
not moved — because B-8 documents three test modules patching
main._SEARCH_ENCODER directly and expecting whichever code reads it to
see the patch. _get_search_encoder still owns them, mutating them
through `main.` attribute assignment rather than the `global` statement
it used when it lived in the same module: `global` only rebinds a name
in the function's *own* module, so once this function moved, the old
`global _SEARCH_ENCODER, _SEARCH_ENCODER_ERROR` line would have created
and mutated a second, disconnected pair of globals inside api.search
instead of main's — invisible to /health and to the three tests that
patch main._SEARCH_ENCODER. Same problem fit_ml has with
CONTENT_VECTOR_REPORT (still parked in main.py for exactly this reason)
except this one has no other entanglement blocking the fix, so it moves
now with the rewrite applied.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, status

import events
import main
from auth import OptionalUser, SessionDep
from services.recommendation import apply_filters

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search")
@router.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=200)) -> Dict[str, Any]:
    items = apply_filters(main.BOOKS, q=q)
    return {"items": items, "total": len(items)}

# ==========================================================================
# Semantic search — section 27
# ==========================================================================
# Kept separate from /api/search, which is the existing keyword filter over
# the in-memory catalogue. They answer different questions: that one finds a
# title you can already name, this one finds a book from a description of
# what you want. Replacing it would break every caller for no gain.

def _get_search_encoder():
    """Load the embedding backend once. Idempotent.

    Called at startup so `/health` can tell the truth about search without a
    request having happened first, and kept lazy-safe so a caller that arrives
    before or instead of that still works.

    Never at import: the fitted artefact is ~48 MB, and an app that cannot
    start because search is unconfigured is worse than one where search alone
    reports unavailable (section 12 — a missing capability degrades, it does
    not cascade). Failure here is recorded, not raised.
    """
    if main._SEARCH_ENCODER is not None or main._SEARCH_ENCODER_ERROR is not None:
        return main._SEARCH_ENCODER

    try:
        from embeddings import get_backend

        backend = get_backend()
        if hasattr(backend, "load") and backend.name == "lsa":
            backend.load()
        main._SEARCH_ENCODER = backend
        log.info(f"semantic search ready (backend={backend.name})")
    except Exception as exc:
        main._SEARCH_ENCODER_ERROR = f"{type(exc).__name__}: {exc}"
        log.warning(f"semantic search unavailable: {main._SEARCH_ENCODER_ERROR}")
    return main._SEARCH_ENCODER


@router.get("/api/search/semantic")
def semantic_search_route(
    user: OptionalUser,
    session: SessionDep,
    q: str = Query(..., min_length=2, max_length=400),
    limit: int = Query(10, ge=1, le=50),
    language: Optional[str] = Query(None, max_length=8),
    genre: Optional[str] = Query(None, max_length=64),
    min_year: Optional[int] = Query(None, ge=0, le=2100),
    max_year: Optional[int] = Query(None, ge=0, le=2100),
    by_passage: bool = Query(False, description="one row per passage, not per book"),
):
    # No return annotation on purpose: this returns a plain dict on success
    # and a JSONResponse when search is unconfigured, matching how the other
    # routes in this module report a degraded capability.
    """Find books from a description of what the reader wants.

    Authorization is not decided here. `user` is optional — anonymous callers
    are a supported case — and the visibility rule lives in one place,
    `search.visible_chunks`, which every retrieval path goes through. An
    endpoint cannot opt out of it by forgetting a filter.
    """
    encoder = _get_search_encoder()
    if encoder is None:
        # 503 rather than 500: this is a configuration state with a known
        # remedy, not a crash, and the message says what the remedy is.
        return main.error_response(
            "Semantic search is not configured on this instance. "
            "Run `python -m embed_pass --fit` to build the model.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    from search import search_books, search_chunks

    vector = encoder.encode([q])[0]
    filters = dict(
        language=language,
        genre=genre,
        min_year=min_year,
        max_year=max_year,
        # Never rank the query against vectors from a different model.
        embedding_model=encoder.name,
    )
    user_id = user.id if user else None

    if by_passage:
        hits = search_chunks(session, vector, user_id=user_id, limit=limit, **filters)
        items = [h.as_dict() for h in hits]
    else:
        # search_books now queries book_vectors (F-44), which covers the
        # whole catalogue — not search_chunks collapsed to one row per book,
        # which could only ever find the ~23% of books with a chunk.
        items = search_books(session, vector, user_id=user_id, limit=limit, **filters)

    # F-42. Search queries are the clearest statement of intent a reader ever
    # makes — section 24 lists them under behavioural signals for that reason.
    # The result count matters as much as the query: a search returning
    # nothing is a content gap worth knowing about.
    events.record(
        events.SEARCH,
        user_id=user_id,
        context={
            "q": q,
            "results": len(items),
            "backend": encoder.name,
            "filters": {k: v for k, v in filters.items() if v is not None} or None,
        },
    )

    return {
        "query": q,
        "items": items,
        "total": len(items),
        # Named so a caller can tell which vector space produced the ranking;
        # comparing scores across backends is meaningless.
        "backend": encoder.name,
        # Honest about scope, and no longer the same answer for both modes:
        # passage search still only covers books with a chunk (~23% of the
        # catalogue, growing as OI-7's enrichment fills it in); book-level
        # search now covers all of it via book_vectors (F-44). An empty list
        # means nothing matched, never that the book does not exist.
        "scope": "embedded passages only" if by_passage else "full catalogue",
    }

