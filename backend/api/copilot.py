"""Reading Copilot — POST /api/books/{book_id}/ask — section 32, Phase 5.

Thin: it resolves the encoder and the model, then hands off to
`services.copilot.ask_about_book`, which owns the pipeline and the
grounding checks. The reasoning for those lives there.

Section 33's memory is wired in here, at the API layer, and nowhere lower
— `services.copilot` still does not know this module exists, matching its
own docstring's reasoning for keeping memory a layer on top rather than
smuggled in. This is the layer: read this reader's recent memory for this
book before asking, pass it in as `prior_context`; after a real answer,
summarise the turn and record it. An anonymous caller reads and writes
nothing — `services.memory` already no-ops for `user_id=None`, so this
just calls it the same way for every caller.

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
from services.memory import format_prior_context, recent_memory, record_turn
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

    # Section 33. Empty (and cheap: `format_prior_context` short-circuits)
    # for an anonymous caller or a first conversation about this book.
    prior_context = format_prior_context(
        recent_memory(session, user_id=account_id, book_id=book_id)
    )

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
            prior_context=prior_context,
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

    # Recorded after a real answer exists, on a best-effort basis: a
    # memory-write failure must not turn a successful, already-grounded
    # answer into a 500 (F-21's rule, applied here too). A second, separate
    # slot acquisition — summarising one short sentence is cheap, and this
    # keeps peak GPU concurrency at one request at a time rather than
    # holding the main answer's slot open longer for an unrelated call. If
    # the slot is saturated, `summarize_turn`'s own `llm=None` fallback
    # still records a plainer entry rather than losing the turn entirely.
    summarizer = None
    if provider is not None:
        try:
            with llm_module.slot():
                summarizer = provider
                record_turn(
                    session, user_id=account_id, book_id=book_id,
                    question=payload.question, answer=result["answer"],
                    llm=summarizer,
                )
        except Exception as exc:
            log.warning(
                f"copilot: memory summarisation unavailable for book {book_id}, "
                f"falling back: {exc}"
            )
            summarizer = None
    if provider is None or summarizer is None:
        try:
            record_turn(
                session, user_id=account_id, book_id=book_id,
                question=payload.question, answer=result["answer"], llm=None,
            )
        except Exception as exc:
            log.warning(f"copilot: memory write failed for book {book_id}: {exc}")

    return result
