"""Audiobook generation — GET/POST /api/audiobook/*.

Extracted verbatim from `main.py` in the Phase D restructure, from two
non-adjacent locations: audiobook_info, audiobook_generate and
audiobook_job_status sat together; audiobook_stream sat far below,
after the comments and reading routes. All four share one URL prefix,
so they join here regardless of where main.py had them.

Bare BOOK_BY_ID, RECOMMENDER, error_response and AUDIO_DIR become
main.<name> — main.py's live module state (RESTRUCTURE-NOTES B-4, 5.2).
AUDIO_DIR here is main.py's own constant (`backend/audio_outputs`,
computed from main.py's own location, which has not moved), a
different object from `AudiobookEngine.AUDIO_DIR` in services/audio.py
— the two must resolve to the same directory (B-6) but are not the
same name.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse, JSONResponse

import jobs
import main
import ratelimit
from schemas.audiobook import AudiobookRequest

router = APIRouter()


@router.get("/api/audiobook/{book_id}")
def audiobook_info(book_id: int):
    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)
    return {
        "ok": True,
        "book": b,
        "eligible": b.get("pages", 0) > 0,
        "message": f"'{b['title']}' is ready for audiobook generation.",
    }

@router.post("/api/audiobook/generate")
def audiobook_generate(payload: AudiobookRequest, request: Request):
    # OI-5. This endpoint is unauthenticated (F-07 covered the rest of the
    # API, not this one) and synchronous (F-19): every call fetches a book and
    # synthesises speech in the request thread. A few repeat calls occupy
    # every worker, so it is a denial-of-service lever that needs no
    # credentials. The limit goes first, before any work is done.
    try:
        ratelimit.hit(
            f"ratelimit:audiobook:{ratelimit.client_ip(request)}",
            ratelimit.AUDIOBOOK_LIMIT,
            ratelimit.AUDIOBOOK_WINDOW,
        )
    except ratelimit.RateLimited as limited:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": str(limited)}},
            headers={"Retry-After": str(limited.retry_after)},
        )

    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'audiobook', None):
        return main.error_response("Audiobook engine not ready.")

    # F-19. Synthesis used to run here, in the request thread, with no
    # timeout — three concurrent calls hung the entire test suite past 120
    # seconds. It now returns immediately with a job to poll.
    try:
        job = jobs.REGISTRY.submit(
            main.RECOMMENDER.audiobook.generate,
            book_name=payload.book_name,
            book_id=payload.book_id,
            lang=payload.language,
        )
    except jobs.Saturated as full:
        # A queue that accepts everything only moves the exhaustion. Say no.
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"message": str(full)}},
            headers={"Retry-After": str(full.retry_after)},
        )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "ok": True,
            "job_id": job.id,
            "status": job.status,
            "poll": f"/api/audiobook/jobs/{job.id}",
        },
        headers={"Location": f"/api/audiobook/jobs/{job.id}"},
    )


@router.get("/api/audiobook/jobs/{job_id}")
def audiobook_job_status(job_id: str):
    """Poll a generation job — F-19's other half.

    404 means the job never existed *or* its result has aged out of the
    registry (15 minutes). Those are deliberately indistinguishable: a client
    that waited that long should re-request rather than be told to keep
    polling something that is gone.
    """
    job = jobs.REGISTRY.get(job_id)
    if job is None:
        return main.error_response("No such job.", status.HTTP_404_NOT_FOUND)
    return job.to_dict()



@router.get("/api/audiobook/{book_id}/stream")
def audiobook_stream(book_id: int):
    """فرانت‌اند این رو صدا میزنه - استریم فایل صوتی"""
    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    # F-06: this used to serve a single global Path("audiobook.mp3") from the
    # process CWD, ignoring book_id entirely — every book streamed whatever
    # was generated last. It now reads the per-book file that
    # AudiobookEngine.generate writes. book_id is a validated int, so it
    # cannot escape the directory.
    audio_file = main.AUDIO_DIR / f"{book_id}.mp3"
    if not audio_file.exists() or audio_file.stat().st_size == 0:
        return main.error_response(
            "No audiobook generated for this book yet.",
            status.HTTP_404_NOT_FOUND,
        )

    return FileResponse(audio_file, media_type="audio/mpeg")
