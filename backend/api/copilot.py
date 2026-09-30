"""Reading Copilot — POST /api/books/{book_id}/ask — section 32, Phase 5.

Thin: it resolves the encoder and the model, then hands off to
`services.copilot.ask_about_book`, which owns the pipeline and the
grounding checks. The reasoning for those lives there.

`OptionalUser`, not `CurrentUser`, and that is the deliberate part:
`search_chunks` already decides what a caller may see, so an anonymous
reader can ask about a public-domain book while a signed-in one
additionally reaches their own uploads — the same rule
`/api/search/semantic` already follows. No id is accepted from the caller;
that would be the mistake `services/librarian.py`'s docstring describes at
length.

Rate-limited on the same grounds as `/api/chat`: one question here is one
generation against the single local GPU, and a per-caller ceiling is what
stops one client occupying the queue (OI-5).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

import main
import ratelimit
from api.search import _get_search_encoder
from auth import OptionalUser, SessionDep
from schemas.copilot import AskRequest
from services.copilot import ask_about_book
from services.providers import llm as llm_module
from services.providers.llm import LLMUnavailable, get_llm_provider

log = logging.getLogger("api.copilot")

router = APIRouter()


@router.post("/api/books/{book_id}/ask")
def ask_about_this_book(
    book_id: int,
    payload: AskRequest,
    request: Request,
    user: OptionalUser,
    session: SessionDep,
):
    """Answer one question about one book, from that book's own passages."""
    account_id = user.id if user else None
    try:
        ratelimit.hit_all(
            ratelimit.caller_keys("copilot", request, account_id),
            ratelimit.CHAT_LIMIT,
            ratelimit.CHAT_WINDOW,
        )
    except ratelimit.RateLimited as limited:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": str(limited)}},
            headers={"Retry-After": str(limited.retry_after)},
        )

    encoder = _get_search_encoder()
    if encoder is None:
        # 503, not 500: a configuration state with a stated remedy, the same
        # answer /api/search/semantic gives.
        return main.error_response(
            "Semantic search is not configured on this instance, so there are "
            "no passages to answer from. Run `python -m embed_pass --fit`.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    vector = encoder.encode([payload.question])[0]

    # The model is optional by design: retrieval alone is a useful, honest
    # answer shape (`services.copilot` returns the citations either way), so
    # a model outage degrades this endpoint rather than failing it.
    def _answer(llm):
        return ask_about_book(
            session,
            book_id=book_id,
            question=payload.question,
            query_vector=vector,
            user_id=account_id,
            llm=llm,
            embedding_model=encoder.name,
        )

    try:
        provider = get_llm_provider()
    except Exception as exc:  # pragma: no cover - construction is trivial
        log.warning(f"copilot: no LLM provider available: {exc}")
        provider = None

    try:
        if provider is None:
            result = _answer(None)
        else:
            # The same concurrency guard the librarian takes: one GPU, and a
            # burst of readers should queue rather than all time out.
            with llm_module.slot():
                result = _answer(provider)
    except LLMUnavailable as exc:
        # Every model down, or over capacity. Retrieval still happened, so
        # answer with the passages and say the assistant is the missing part.
        log.warning(f"copilot: model unavailable for book {book_id}: {exc}")
        result = _answer(None)

    if not result.get("ok", True):
        return main.error_response(result.get("error", "could not answer"))
    return result
