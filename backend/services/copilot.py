"""Reading Copilot — prmpt.md section 32, Phase 5.

Section 32's pipeline, in order: a reader's question, book-scoped
retrieval, the passages that came back, an LLM, a grounded answer, a
citation. The retrieval step is `search.search_chunks(book_id=...)`; the
grounding discipline is this module's own.

**The label is checked, not taken on trust.** Section 32 requires the
assistant to distinguish `Known from book` / `Inference` / `Uncertain`.
The easy reading of that is to ask the model to say which and print the
answer — which would make the most load-bearing field in the response the
one nothing verifies. This project has been here before: F-22's answer to
"the model might name a book it never saw" was not to ask it nicely
(see `librarian._grounding_problem`). Two checks are cheap and real:

1. **No passages, no knowledge.** If retrieval returned nothing there is
   nothing in the book to have known, so the label cannot be `known`
   whatever the model says. The model is not called at all in that case —
   there is nothing to ground on, and asking anyway is how a confident
   answer about an unavailable book gets produced.
2. **A quoted span must be in a passage.** If the answer presents words as
   the book's own, those words have to appear in something retrieval
   actually returned. Same guard, same normalisation as the librarian's,
   applied to passages instead of titles. An unsupported quote downgrades
   the label and is reported — it does not silently pass.

Two things this deliberately does not do:

- **It does not serve the book's pages.** Passages reach the reader as
  citations supporting an answer. That is retrieval, which OI-4's posture
  permits for an upload; rendering the book back is what it does not.
- **It does not remember anything between calls.** Section 33's
  per-(user, book) memory is its own feature with its own storage. This
  loop is stateless, and a summarised-memory layer belongs on top of it
  rather than smuggled into it.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from services.search import search_chunks

log = logging.getLogger("services.copilot")

# Over-fetch then trim: the passages that answer a question about one book
# are not always its nearest neighbours to the question's own wording.
RETRIEVAL_POOL = 12
MAX_CITATIONS = 4

# Deliberately lower than `search.MIN_SIMILARITY`: catalogue-wide search is
# choosing between 6,000 books and can afford to be strict, whereas here the
# book is already decided and the only question is which of *its* passages
# are relevant.
MIN_PASSAGE_SIMILARITY = 0.02

KNOWN = "known_from_book"
INFERENCE = "inference"
UNCERTAIN = "uncertain"

SYSTEM_PROMPT = """You are a reading assistant answering a question about one specific book, using only the passages provided from that book.

Rules, without exception:
- Answer only from the passages given. They are the book's own text.
- If the passages do not contain the answer, say so plainly. Do not fill the gap from memory of this book or any other.
- When you quote the book, put the quoted words in double quotes and copy them exactly as they appear in a passage. Do not paraphrase inside quotation marks.
- End your reply with exactly one line, nothing after it:
  GROUNDING: known_from_book
  or
  GROUNDING: inference
  or
  GROUNDING: uncertain
- Use known_from_book only when the passages state the answer. Use inference when you reasoned from them to something they do not state outright. Use uncertain when they do not support an answer.
- Keep the answer to a few sentences."""

_LABEL_LINE = re.compile(
    r"^\s*GROUNDING:\s*(known_from_book|inference|uncertain)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_QUOTED = re.compile(r'"([^"\n]{3,300})"|“([^”\n]{3,300})”')


def _norm(text: str) -> str:
    """Same normalisation the librarian's grounding guard uses: compare on
    words, so punctuation and spacing differences are not mistaken for the
    model inventing text."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def unsupported_quotes(answer: str, passages: list[str]) -> list[str]:
    """Quoted spans in `answer` that appear in none of `passages`.

    A quote is the one claim in an answer that is checkable word for word,
    which is why the prompt asks for quotation marks at all. Spans under
    three words are skipped: a two-word quotation is emphasis or a term of
    art, not a claim about the text.
    """
    haystack = " ".join(_norm(p) for p in passages)
    bad: list[str] = []
    for match in _QUOTED.finditer(answer):
        span = next(g for g in match.groups() if g)
        needle = _norm(span)
        if len(needle.split()) < 3:
            continue
        if needle and needle not in haystack:
            bad.append(span)
    return bad


def _strip_label(answer: str) -> str:
    return _LABEL_LINE.sub("", answer).strip()


def _claimed_label(answer: str) -> Optional[str]:
    matches = _LABEL_LINE.findall(answer)
    return matches[-1].lower() if matches else None


def ask_about_book(
    session,
    *,
    book_id: int,
    question: str,
    query_vector,
    user_id: Optional[int] = None,
    llm=None,
    embedding_model: Optional[str] = None,
    prior_context: Optional[str] = None,
) -> dict[str, Any]:
    """One turn of section 32's pipeline. Returns a plain dict.

    `query_vector` and `llm` are injected rather than built here, for the
    same reason `LibrarianDeps` injects its dependencies: this stays
    testable without an encoder or a model, and never imports `main`.

    `prior_context` (section 33): plain text prepended to the prompt, ahead
    of the retrieved passages. This function does not build it, store it,
    or know it came from `services/memory.py` — the same reason this
    module's own docstring gives for keeping memory a layer on top rather
    than smuggled in here. `None` (every caller before this parameter
    existed) leaves every existing test's behaviour unchanged.
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "question is required"}

    hits = search_chunks(
        session,
        query_vector,
        user_id=user_id,
        book_id=book_id,
        limit=RETRIEVAL_POOL,
        min_similarity=MIN_PASSAGE_SIMILARITY,
        embedding_model=embedding_model,
    )

    citations = [
        {
            "chunk_id": h.chunk_id,
            "ordinal": h.ordinal,
            "passage": h.passage,
            "similarity": round(h.similarity, 4),
            "origin": h.origin,
        }
        for h in hits[:MAX_CITATIONS]
    ]

    # Check 1. Nothing retrieved means nothing in this book to have known,
    # and no reason to spend a model call finding that out.
    if not hits:
        return {
            "ok": True,
            "book_id": book_id,
            "grounding": UNCERTAIN,
            "answer": (
                "I do not have any of this book's text to answer from, so I "
                "cannot say. That is either because the book has no stored "
                "text yet, or because none of it is relevant to the question."
            ),
            "citations": [],
            "passages_considered": 0,
            "model": None,
            "label_downgraded_from": None,
            "unsupported_quotes": [],
        }

    if llm is None:
        # Retrieval worked and is worth returning on its own; the answer is
        # what is missing. Section 12's shape: degrade, do not fail.
        return {
            "ok": True,
            "book_id": book_id,
            "grounding": UNCERTAIN,
            "answer": "The reading assistant is not configured on this instance.",
            "citations": citations,
            "passages_considered": len(hits),
            "model": None,
            "label_downgraded_from": None,
            "unsupported_quotes": [],
        }

    passages = [h.passage for h in hits]
    numbered = "\n\n".join(
        f"[passage {i}, position {h.ordinal}]\n{h.passage}"
        for i, h in enumerate(hits, 1)
    )
    context_block = f"{prior_context}\n\n" if prior_context else ""
    response = llm.chat([
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{context_block}Passages from the book:\n\n{numbered}\n\nQuestion: {question}"
            ),
        },
    ])

    raw = response.content or ""
    claimed = _claimed_label(raw)
    answer = _strip_label(raw)

    # Check 2. Words presented as the book's own have to be in a passage.
    bad_quotes = unsupported_quotes(answer, passages)

    label = claimed or UNCERTAIN
    downgraded_from = None
    if bad_quotes and label == KNOWN:
        downgraded_from, label = KNOWN, UNCERTAIN
        log.warning(
            f"copilot: book {book_id} answer quoted {len(bad_quotes)} span(s) "
            "no retrieved passage contains; label downgraded"
        )
    if claimed is None:
        log.warning(f"copilot: book {book_id} answer carried no GROUNDING line")

    return {
        "ok": True,
        "book_id": book_id,
        "grounding": label,
        "answer": answer or "I cannot answer that from this book's text.",
        "citations": citations,
        "passages_considered": len(hits),
        "model": getattr(response, "model", None),
        # Reported rather than hidden: a caller is entitled to know the label
        # was not the model's own claim.
        "label_downgraded_from": downgraded_from,
        "unsupported_quotes": bad_quotes,
    }
