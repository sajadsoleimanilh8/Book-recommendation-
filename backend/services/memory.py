"""AI Book Memory — section 33, Phase 5.

Section 33's pipeline: conversation, periodic summarization, structured
memory. Explicit constraint: "do not replay raw transcripts forever."

**Enforced at storage, not just in the prompt.** `record_turn` never
receives or stores the raw question or the model's full answer — only a
short, LLM-produced summary line, bounded in length (`MAX_SUMMARY_CHARS`).
A caller cannot accidentally regress this into transcript storage by
passing something longer; it is truncated. One row per turn, append-only,
so a reader's memory of a book is a real, inspectable history rather than
one mutable blob a later write could silently overwrite.

Deliberately its own layer, not folded into `services/copilot.py`
(`copilot`'s own docstring: "a summarised-memory layer belongs on top of it
rather than smuggled into it"): `copilot.ask_about_book` does not know this
module exists. The caller — `api/copilot.py` — reads memory before asking,
passes it in as plain prior context, and writes a new summary after. That
keeps the grounding-critical path (what the book says) and the
continuity-only path (what we discussed about it) independently testable
and independently disable-able.

**Summarization is not grounded against the book, and does not need to
be.** It compresses a conversation that already happened — the question the
reader asked, and the answer the copilot already produced (which was
itself grounded when it was produced) — it does not introduce a new claim
about the book's content. The one property that matters here is brevity,
not faithfulness-to-the-text, which is why this module carries no
quote-checking of its own.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from models import BookMemory

log = logging.getLogger("services.memory")

# A structured note, not a transcript. Section 33's own example ("previously
# asked about the narrator's motivation in Chapter 4") is one short sentence;
# this is generous headroom over that, not an invitation to store more.
MAX_SUMMARY_CHARS = 240

# How much prior context reaches a new turn's prompt. Bounded for the same
# reason RETRIEVAL_POOL is bounded in copilot.py: unbounded history is a
# transcript by another name, and a long-running conversation should not
# make every subsequent prompt larger forever.
RECENT_MEMORY_LIMIT = 5

_SUMMARY_PROMPT = """Summarise this one exchange about a book in a single short sentence, from the reader's point of view, in the past tense. State only what was asked and the gist of the answer — do not add anything the answer did not say.

Question: {question}
Answer: {answer}

One sentence, no preamble:"""


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    # A hard cut at a word boundary reads as an honest truncation rather
    # than a sentence that trails into nothing.
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def summarize_turn(llm, question: str, answer: str) -> str:
    """One short, past-tense sentence describing a Q&A exchange.

    Falls back to a deterministic, non-LLM summary when no model is
    available — the same "degrade, do not fail" shape the rest of section
    12 already follows — so a memory entry is still recorded even during a
    model outage, just a plainer one.
    """
    if llm is None:
        return _truncate(f"Asked: {question}", MAX_SUMMARY_CHARS)
    try:
        response = llm.chat([
            {"role": "user", "content": _SUMMARY_PROMPT.format(
                question=question.strip(), answer=answer.strip()
            )},
        ])
        summary = (response.content or "").strip()
    except Exception as exc:  # section 12: memory must degrade, not crash the turn
        log.warning(f"memory: summarisation failed, using fallback: {exc}")
        summary = ""
    if not summary:
        summary = f"Asked: {question}"
    return _truncate(summary, MAX_SUMMARY_CHARS)


def record_turn(
    session, *, user_id: Optional[int], book_id: int, question: str, answer: str, llm=None
) -> Optional[BookMemory]:
    """Summarise and store one turn. A no-op for an anonymous caller —
    memory is per reader, and there is no reader to attach it to.
    """
    if user_id is None:
        return None
    summary = summarize_turn(llm, question, answer)
    entry = BookMemory(user_id=user_id, book_id=book_id, summary=summary)
    session.add(entry)
    session.commit()
    return entry


def recent_memory(session, *, user_id: Optional[int], book_id: int, limit: int = RECENT_MEMORY_LIMIT) -> list[str]:
    """This reader's own structured memory for this book, most recent
    first. Empty for an anonymous caller or a first conversation — an
    honest absence, not an error.
    """
    if user_id is None:
        return []
    rows = session.scalars(
        select(BookMemory.summary)
        .where(BookMemory.user_id == user_id, BookMemory.book_id == book_id)
        .order_by(BookMemory.created_at.desc())
        .limit(limit)
    ).all()
    return list(rows)


def format_prior_context(summaries: list[str]) -> Optional[str]:
    """Turn stored summaries into the plain-text block `copilot.ask_about_book`
    accepts as `prior_context`. `None` when there is nothing to say, so a
    first-ever conversation about a book carries no empty scaffolding into
    the prompt.
    """
    if not summaries:
        return None
    # Oldest first when presented, even though they are stored and fetched
    # most-recent-first — a reader's own history reads as a timeline.
    lines = "\n".join(f"- {s}" for s in reversed(summaries))
    return f"Earlier in this reader's conversations about this book:\n{lines}"
