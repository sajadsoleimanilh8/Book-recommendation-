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

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter
from sqlalchemy import func as sa_func, select as sa_select

import main
import models
from core import config
from db import SessionLocal

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
@router.get("/api/health")
def health():
    return {
        # ok means "safe to serve real traffic": real catalogue AND a fitted
        # ML engine. Reporting ok:True with ml_ready:False would hide a dead
        # recommender behind a green check — the same blind spot as F-03.
        "ok": (
            main.DATA_SOURCE == "json"
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
        # both — serving fabricated books is not a healthy state. "csv_fallback"
        # itself is gone (F-49): it named a CSV chain that never existed in
        # this repository and could never actually be reached.
        "data_source": main.DATA_SOURCE,
        "using_real_data": main.DATA_SOURCE == "json",
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
        # F-48. The seed file labels every Gutenberg book Italian; this says
        # whether the database correction was applied. A skipped overlay is
        # invisible downstream, so it has to be visible here.
        "language_overlay": main.LANGUAGE_OVERLAY or None,
        # F-53: the standing enrichment job ran green for three days doing
        # nothing at all. See _enrichment_health.
        "enrichment": _enrichment_health(),
        # OI-5: the settings that would stop this configuration being
        # deployable. Empty in production by construction — `config` refuses
        # to boot while any remain — so this only ever has content on a
        # developer's machine, which is exactly where it is worth seeing.
        #
        # Gated on DEBUG rather than shown always. /health is public and
        # unauthenticated, and "POSTGRES_PASSWORD is the development default"
        # is a useful sentence to the wrong reader on a box that is exposed
        # without meaning to be. Nothing is lost by hiding it: where it would
        # be visible to a stranger it is already empty.
        "production_blockers": config.production_problems() if config.DEBUG else None,
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
        # OI-5: /health is public and unauthenticated. A database exception
        # string here carries the failing SQL and frequently the connection
        # target, so the text is kept for development and the log only.
        log.warning(f"corpus backend probe failed: {exc}")
        return {
            "searchable_chunks": None,
            "corpus_backend_error": str(exc)[:120] if config.DEBUG else "unavailable",
        }

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


# F-53 --------------------------------------------------------------------
#
# The standing enrichment job ran 2026-09-17 -> 2026-09-20 logging
# `processed 0, throttled True`, exit code 0, Task Scheduler "Last Result: 0".
# Every component behaved correctly — Google answered 403, the F-29 circuit
# breaker stopped after 3 throttles, rows were left `pending` rather than
# falsely `not_found`. Nothing was corrupted and nothing was wrong. The
# *outcome* was 72 hours of zero progress that looked identical to success
# from every angle a human would check.
#
# The agreed fix was "after 3 consecutive barren runs, warn in /health".
# This implements that intent against ground truth rather than a run counter,
# and the difference is worth stating because it is a deviation:
#
#   A run counter records what the job *says* it did. F-53 is a case where
#   the job said it succeeded. Adding a table of run outcomes would have
#   given a second thing that can report success while the data sits still —
#   the same class of claim, one layer up.
#
#   `max(enriched_at)` cannot drift from reality, because it *is* the
#   reality: the last moment any book's enrichment state changed. Combined
#   with "work remains", it answers the only question worth asking.
#
# It also needs no new table, no new writer, and no coordination with a
# process that runs outside the app.
STALL_DAYS = 3


def _enrichment_health() -> Dict[str, Any]:
    """Whether the standing backfill is actually making progress.

    Never raises: /health returning 500 is F-21, and a monitoring endpoint
    that fails when the thing it monitors is unhealthy is worse than none.
    """
    try:
        with SessionLocal() as session:
            pending, last = session.execute(
                sa_select(
                    sa_func.count()
                    .filter(models.Book.enrichment_status == "pending"),
                    sa_func.max(models.Book.enriched_at),
                ).where(models.Book.owner_id.is_(None))
            ).one()
    except Exception as exc:
        log.warning(f"enrichment health probe failed: {exc}")
        return {"pending": None, "error": "unavailable"}

    # No book has ever been enriched. That is a fresh install, not a stall,
    # and the two are indistinguishable from here — so report the empty state
    # honestly (F-17) rather than raising a false alarm on a machine where
    # the job was simply never set up.
    if last is None:
        return {"pending": pending, "last_progress": None, "stalled": False}

    age = datetime.now(timezone.utc) - last
    days = round(age.total_seconds() / 86400, 2)
    # Pending is the second half deliberately: once the backlog is drained,
    # a long quiet period is the job having finished, not the job being
    # broken, and warning then would train everyone to ignore this field.
    stalled = days >= STALL_DAYS and pending > 0

    return {
        "pending": pending,
        "last_progress": last.isoformat(),
        "days_since_progress": days,
        "stalled": stalled,
        "warning": (
            f"enrichment has made no progress in {days} days with {pending} "
            f"book(s) still pending — the job may be running green and doing "
            f"nothing (F-53)"
        ) if stalled else None,
    }
