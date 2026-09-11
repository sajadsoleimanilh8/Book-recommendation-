"""Health check — GET /health, /api/health.

Extracted verbatim from `main.py` in the Phase D restructure, with one
necessary transformation: every bare reference to DATA_SOURCE, BOOKS,
RECOMMENDER, QUESTIONER, _SEARCH_ENCODER, _SEARCH_ENCODER_ERROR and
CONTENT_VECTOR_REPORT is now `main.<name>`. These are main.py's live
module state, rebound by startup() after this module is imported
(RESTRUCTURE-NOTES B-4) — a snapshot import at module load time would
read them before startup() ever runs and see stale initial values
forever. `import main` (not `from main import X`) and read the
attribute at call time, same contract conftest.py and three test
modules already depend on when they read main.RECOMMENDER,
main._SEARCH_ENCODER, etc. after the app has booted (RESTRUCTURE-NOTES
5.2, B-8). This is the router-equivalent of the `__file__`-derived path
re-anchors (B-6) elsewhere in this restructure: the code is unchanged,
only how it reaches something that moved.

`import main` here does not deadlock the circular import with main.py
including this router: Python registers a module in sys.modules before
executing its body, so by the time main.py's `from api.health import
router` reaches this file, `main` already exists in sys.modules (mid
-import) for this file's bare `import main` to bind to. Nothing here
reads a main.* attribute until a request actually arrives, long after
main.py has finished importing and startup() has run.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import func as sa_func, select as sa_select

import main
import models
from db import SessionLocal

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
def health():
    return {
        # ok means "safe to serve real traffic": real catalogue AND a fitted
        # ML engine. Reporting ok:True with ml_ready:False would hide a dead
        # recommender behind a green check — the same blind spot as F-03.
        "ok": (
            main.DATA_SOURCE in ("json", "csv_fallback")
            and len(main.BOOKS) > 0
            and main.RECOMMENDER is not None
            and getattr(main.RECOMMENDER, "_fitted", False)
        ),
        "books_loaded": len(main.BOOKS),
        "ml_ready": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, '_fitted', False),
        "audiobook_ready": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, 'audiobook', None) is not None,
        "comments_ready": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, 'comment', None) is not None,
        "chatbot_ready": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, 'chatbot', None) is not None,
        "reminder_ready": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, 'reminder', None) is not None,
        "questioner_ready": main.QUESTIONER is not None,
        # F-03: this used to report only "json" or "csv_fallback", so a boot
        # onto synthetic data was indistinguishable from a healthy one.
        # "synthetic" and "none" are now reportable, and `ok` is False for
        # both — serving fabricated books is not a healthy state.
        "data_source": main.DATA_SOURCE,
        "using_real_data": main.DATA_SOURCE in ("json", "csv_fallback"),
        # F-21: this was int(main.RECOMMENDER.cluster), but `cluster` is the
        # ClusteringModel object, not a number — so /health raised a 500
        # TypeError on every call where the ML engine had actually fitted.
        # The endpoint only ever returned 200 while the engine was broken,
        # which is a large part of why F-03 went unnoticed. best_k is the int.
        "clusters": getattr(getattr(main.RECOMMENDER, "cluster", None), "best_k", None),
        # Semantic search reports separately and does NOT gate `ok`. It
        # degrades cleanly to 503 on its own endpoint while the rest of the
        # app serves normally (section 12), so folding it into `ok` would
        # take the whole service red over one optional capability.
        #
        # But it is reported, because the F-03 and F-21 lesson is that a
        # capability nobody can see the state of is a capability that fails
        # silently. `search_ready:false` next to `ok:true` is the honest
        # shape: the app is healthy, this feature is not configured.
        "search_ready": main._SEARCH_ENCODER is not None,
        "search_backend": getattr(main._SEARCH_ENCODER, "name", None),
        "search_error": main._SEARCH_ENCODER_ERROR,
        # F-39's other half. A query encoded by one backend against chunks
        # embedded by another is filtered out by `search_books`, so the result
        # is an empty list rather than nonsense — safe, but baffling: search
        # answers 200 with nothing, for every query, and nothing says why.
        #
        # This names the mismatch. `searchable_chunks` counts only the chunks
        # the active encoder can actually reach, which is the number that
        # matters, rather than the total.
        **_search_corpus_health(),
        # F-44 + the F-41 lesson: a silent fallback to the weaker content
        # space looks exactly like working. Name which one is live.
        "content_similarity_space": getattr(main.RECOMMENDER, "content_space", None),
        "content_vectors": main.CONTENT_VECTOR_REPORT or None,
        # F-26: how much real reading evidence the ranker is learning from.
        # Zero is expected until there is traffic, and means the target is
        # still the popularity prior — visible here so nobody mistakes the
        # new target for an active one before it has data.
        "reading_depth": main.READING_DEPTH_REPORT or None,
    }


def _search_corpus_health() -> Dict[str, Any]:
    """What the active encoder can actually see in the corpus."""
    name = getattr(main._SEARCH_ENCODER, "name", None)
    if name is None:
        return {"searchable_chunks": None, "corpus_backend_mismatch": None}
    try:
        with SessionLocal() as session:
            counts = dict(
                session.execute(
                    sa_select(models.BookChunk.embedding_model, sa_func.count())
                    .where(models.BookChunk.embedding.isnot(None))
                    .group_by(models.BookChunk.embedding_model)
                ).all()
            )
    except Exception as exc:
        return {"searchable_chunks": None, "corpus_backend_error": str(exc)[:120]}

    # The stored name may carry a fingerprint ("lsa:501a37e8") while the
    # encoder reports the family ("lsa"). Match on the family.
    reachable = sum(n for m, n in counts.items() if m and m.split(":")[0] == name.split(":")[0])
    total = sum(counts.values())
    return {
        "searchable_chunks": reachable,
        "corpus_chunks": total,
        # True when vectors exist that this encoder cannot use — the state
        # that otherwise looks like "search returns nothing for everything".
        "corpus_backend_mismatch": total > 0 and reachable == 0,
        "corpus_backends": {m: n for m, n in counts.items() if m},
    }

