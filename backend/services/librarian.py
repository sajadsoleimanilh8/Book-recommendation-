"""The AI Librarian's tool loop — F-22 Phase C, prmpt.md section 28.

Section 28: "The AI Librarian is a tool-using layer... Do not solve this using a
single prompt." The model here never answers from its own knowledge of books.
It composes calls to four tools, each backed by something that already
exists and is already tested, and the answer is built from what they return:

    search_catalog        book_vectors semantic search (Phase A, 100% coverage)
    check_availability    price_and_availability (F-36's shared rule)
    check_user_library    the caller's own progress and comments
    get_reading_profile   the caller's in-memory taste profile

Three decisions worth stating once, here, because they are easy to undo by
accident:

**No tool takes a user id.** Section 28's sketch has `check_user_library(
user_id)`. Here the identity comes from the authenticated request and nothing
else. A model that can pass a `user_id` argument can be talked into passing
someone else's, and "what has user 7 been reading" is exactly the read the F-07
authorization sweep closed. The tools are unable to ask the question.

**Book data reaches the reader from tool results, never from the model's
text.** The `books` list in the response is assembled from the catalogue rows
the tools returned. The model's prose is a caption on that list, not a source
for it — see `_grounding_problem` for the guard, and its honest limits.

**Failure of the model is not failure of the app.** `LLMUnavailable` (every
model in the chain down) propagates to `ChatbotEngine`, which falls back to
its classify-and-template behaviour (section 12). A *bug* here is not caught
that way, deliberately: swallowing it into a working fallback is the F-30
mistake (`except Exception: continue` turning one error into all of them).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from services.providers.llm import LLMProvider, LLMUnavailable

log = logging.getLogger(__name__)

MAX_STEPS = 4
SEARCH_POOL = 40          # over-fetch, then filter: exclusions eat results
DEFAULT_RESULTS = 6
MAX_RESULTS = 10

SYSTEM_PROMPT = """You are the DigiKitab librarian. You help readers find books in this catalogue.

Rules, without exception:
- Never name a book from your own memory. Only recommend books that a tool returned in this conversation.
- To find or recommend books from the shared catalogue, call search_catalog. To exclude books the reader has already read, call check_user_library first, then pass exclude_read=true.
- For anything about a book the reader uploaded themselves — its content, a summary, a detail, a quote — call search_my_library, never search_catalog. The catalogue and the reader's own uploads are different collections; do not mix results from one into an answer about the other.
- For any question about price or availability, call check_availability for that book. Never state either otherwise. This does not apply to the reader's own uploads, which are not for sale.
- To talk about a book, use only its summary from search_catalog, or its passage from search_my_library. If neither tool returned one, say so plainly.
- Write every book title inside double quotes, exactly as the tool returned it, e.g. "Notes on Grief".
- If a search returns nothing suitable, say so plainly. Do not fill the gap with books you remember.
- Keep the reply short: one or two sentences of framing, then the books. Do not repeat the reader's question."""

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": (
                "Semantic search over the whole book catalogue. Use for any request to "
                "find, suggest or recommend books, including 'like X but Y'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "what the reader wants, in plain words"},
                    "language": {"type": "string", "description": "language code, default 'en'; 'any' for all"},
                    "exclude_authors": {"type": "array", "items": {"type": "string"}},
                    "exclude_read": {
                        "type": "boolean",
                        "description": "drop books already in the reader's library",
                    },
                    "limit": {"type": "integer", "description": f"1-{MAX_RESULTS}, default {DEFAULT_RESULTS}"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Price and availability of one book, by the id search_catalog returned.",
            "parameters": {
                "type": "object",
                "properties": {"book_id": {"type": "integer"}},
                "required": ["book_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_user_library",
            "description": "Books the current reader has already read, started or reviewed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_reading_profile",
            "description": "The current reader's stated tastes: mood, favourite genres and authors.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_my_library",
            "description": (
                "Search inside the reader's own uploaded books — their private library, "
                "not the shared catalogue. Use this for any question about a book the "
                "reader uploaded, or that should be answered from its actual content "
                "(a summary, a detail, a quote), never search_catalog for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "what to find within the reader's own uploaded books",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


@dataclass
class LibrarianDeps:
    """Everything the tools touch, injected — so the loop is testable with
    fakes and never imports `main` at module load (RESTRUCTURE-NOTES B-4)."""

    # (query, pool) -> [{"pk": db book id, "similarity": float}], best first
    search: Callable[[str, int], list[dict[str, Any]]]
    book_for_pk: Callable[[int], Optional[dict[str, Any]]]
    book_by_id: Callable[[int], Optional[dict[str, Any]]]
    # account id -> API book ids the reader has read, started or reviewed
    library_for: Callable[[int], set[int]]
    # Phase 4, section 29/31: (account_id, query, pool) -> the reader's own
    # uploaded chunks, best first. Same "no tool takes a user id" rule as
    # every other tool here — `account_id` is a parameter of the *dependency
    # function*, filled in from `ctx.account_id` at the call site
    # (`_tool_private_library`), never from the model's own arguments.
    # Defaulted so every existing `LibrarianDeps(...)` construction — real
    # or a test's fake — keeps working unchanged; a reader simply gets no
    # private results until a real search_private is wired in.
    search_private: Callable[[int, str, int], list[dict[str, Any]]] = (
        lambda account_id, query, pool: []
    )


@dataclass
class LibrarianResult:
    message: str
    books: list[dict[str, Any]]
    model: str
    steps: int
    grounded: bool


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


_QUOTED = re.compile(r'"([^"\n]{3,140})"|“([^”\n]{3,140})”|\*\*([^*\n]{3,140})\*\*')


def _grounding_problem(
    answer: str, seen_titles: list[str], user_message: str = ""
) -> Optional[str]:
    """A quoted, title-shaped span in the answer that no tool returned.

    The prompt asks for every title in double quotes precisely so this can be
    checked. It catches the failure that matters — the model naming a book from
    memory — for the way it is asked to name books. It does NOT catch a title
    written without quotes, and a model that ignores the formatting rule can
    slip one past. That limit is why the `books` list never depends on the
    prose at all (below): the guard reduces how often a bad caption reaches
    the reader, and cannot be what makes the *data* safe.
    """
    known = [_norm(t) for t in seen_titles]
    said = _norm(user_message)
    for match in _QUOTED.finditer(answer):
        span = next(g for g in match.groups() if g)
        candidate = _norm(span)
        if len(candidate.split()) < 2:
            continue  # a single quoted word is emphasis, not a title
        if candidate in said:
            continue  # the reader named it; echoing it back invents nothing
        if not any(candidate in k or k in candidate for k in known if k):
            return span
    return None


_CURRENCY = re.compile(r"[$€£]\s?(\d[\d,]*(?:\.\d+)?)")
_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
_LIST_MARKER = re.compile(r"(?m)^\s*\d+[.)]\s")
_PRICE_WORDS = re.compile(
    r"\b(prices?|priced|costs?|costing|in stock|currently available|is available|"
    r"are available|availability)\b",
    re.IGNORECASE,
)


def _facts_problem(
    answer: str,
    user_message: str,
    corpus: str,
    prices: set[float],
    availability_checked: bool,
) -> Optional[str]:
    """A price, year, rating or page count the tools never returned.

    Found the hard way, not designed in advance: the first real run of this
    loop answered "a price tag of $35.00 and is currently available" for a
    book whose price is unknown, after a single tool call that was a search.
    A title guard cannot see that; the model had simply written a plausible
    number. So every figure in the prose has to trace back to tool output.

      * a currency amount must equal a price `check_availability` returned;
      * any price/availability wording requires `check_availability` to have
        been called at all;
      * any other figure with 3+ digits or a decimal point (years, ratings,
        page counts) must appear in the tool results or the reader's own
        message. Small whole numbers are ignored: list numbering and "two
        books" are not book data.

    Errs toward flagging. A false positive costs a deterministic answer built
    from the tool data; a false negative is an invented fact on a reader's
    screen.
    """
    for match in _CURRENCY.finditer(answer):
        amount = float(match.group(1).replace(",", ""))
        if not any(abs(amount - p) < 0.005 for p in prices):
            return f"price {match.group(0)!r} was never returned by check_availability"

    if _PRICE_WORDS.search(answer) and not availability_checked:
        return "price/availability claimed without calling check_availability"

    body = _CURRENCY.sub("", _LIST_MARKER.sub("", answer))
    said = user_message
    for match in _NUMBER.finditer(body):
        token = match.group(1)
        if "." not in token and len(token) < 3:
            continue
        if token in said:
            continue
        pattern = rf"(?<![\d.]){re.escape(token)}" + ("" if "." in token else r"(?!\d)")
        if not re.search(pattern, corpus):
            return f"figure {token!r} does not appear in any tool result"
    return None


_STRAY_ARROW = re.compile(r'"[ 	]*>[ 	]*(?=\w)')


def _clean(text: str) -> str:
    """Remove a known qwen2.5:7b artefact: an opening quote fused with a
    stray `>`, as in `">To Kill a Mockingbird" by Harper Lee`.

    Measured, not assumed: 1 in 32 bare one-sentence answers from the 7b (0 in
    32 from the 3b), and none in the tool-loop answers read while building
    this. Cosmetic, not corrupting — the title survives intact and the
    grounding guard normalises punctuation — but it would render on screen, and
    the pattern is narrow enough (a quote, then `>`, then a word character) that
    stripping it cannot touch legitimate text."""
    return _STRAY_ARROW.sub('"', text).strip()


def _template_answer(books: list[dict[str, Any]]) -> str:
    """Deterministic fallback wording, built only from tool data."""
    if not books:
        return "I couldn't find anything in the catalogue matching that."
    lines = [f'"{b["title"]}" by {b["author"]}' for b in books[:5]]
    return "Here is what I found in the catalogue: " + "; ".join(lines) + "."


class Librarian:
    def __init__(self, llm: LLMProvider, deps: LibrarianDeps):
        self.llm = llm
        self.deps = deps

    # -- tools ---------------------------------------------------------------

    def _tool_search(self, args: dict[str, Any], ctx: "_Context") -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}

        limit = max(1, min(int(args.get("limit") or DEFAULT_RESULTS), MAX_RESULTS))
        language = str(args.get("language") or "en").strip().lower()
        excluded_authors = [
            _norm(a) for a in (args.get("exclude_authors") or []) if isinstance(a, str) and a.strip()
        ]
        read = self.deps.library_for(ctx.account_id) if (
            args.get("exclude_read") and ctx.account_id is not None
        ) else set()

        results: list[dict[str, Any]] = []
        for hit in self.deps.search(query, SEARCH_POOL):
            book = self.deps.book_for_pk(hit["pk"])
            if book is None:
                continue
            if not _language_ok(book, language):
                continue
            author = _norm(str(book.get("author", "")))
            if any(ex and ex in author for ex in excluded_authors):
                continue
            if book["id"] in read:
                continue
            ctx.seen[book["id"]] = book
            entry = {
                "id": book["id"],
                "title": book["title"],
                "author": book["author"],
                "genre": book.get("genre"),
                "year": book.get("published_year") or None,
                # Rounded, and no similarity score: both are the kind of
                # internal detail a model repeats to the reader verbatim
                # ("rated 4.0035747133", "similarity score of 0.753").
                "rating": round(float(book["rating"]), 1) if book.get("rating") else None,
            }
            summary = _summary(book)
            if summary:
                entry["summary"] = summary
            results.append(entry)
            if len(results) >= limit:
                break
        return {"results": results, "count": len(results)}

    def _tool_availability(self, args: dict[str, Any], ctx: "_Context") -> dict[str, Any]:
        try:
            book = self.deps.book_by_id(int(args.get("book_id")))
        except (TypeError, ValueError):
            book = None
        if book is None:
            return {"error": "no such book"}
        ctx.availability_checked = True
        if book.get("price") is not None:
            ctx.prices.add(float(book["price"]))
        return {
            "id": book["id"],
            "title": book["title"],
            "price": book.get("price"),
            "availability": book.get("availability"),
        }

    def _tool_library(self, args: dict[str, Any], ctx: "_Context") -> dict[str, Any]:
        if ctx.account_id is None:
            return {"logged_in": False, "books": [],
                    "note": "the reader is not logged in, so there is no library to check"}
        titles = []
        for book_id in sorted(self.deps.library_for(ctx.account_id)):
            book = self.deps.book_by_id(book_id)
            if book:
                titles.append({"id": book_id, "title": book["title"], "author": book["author"]})
        return {"logged_in": True, "books": titles, "count": len(titles)}

    def _tool_profile(self, args: dict[str, Any], ctx: "_Context") -> dict[str, Any]:
        p = ctx.profile
        if p is None:
            return {"known": False}
        return {
            "known": True,
            "mood": p.mood or None,
            "preferred_genres": list(p.preferred_genres),
            "preferred_authors": list(p.preferred_authors),
            "liked_keywords": list(p.liked_keywords)[-10:],
            "disliked_keywords": list(p.disliked_keywords)[-10:],
        }

    def _tool_private_library(self, args: dict[str, Any], ctx: "_Context") -> dict[str, Any]:
        """Phase 4, section 29/31. `ctx.account_id` — from the authenticated
        request, per `answer()`'s caller — is the only identity this ever
        uses; `args` never carries one, the same rule `check_user_library`
        is built on and for the same reason (module docstring).
        """
        if ctx.account_id is None:
            return {
                "logged_in": False,
                "results": [],
                "note": "the reader is not logged in, so there is no private library to search",
            }
        query = str(args.get("query") or "").strip()
        if not query:
            return {"error": "query is required"}

        limit = max(1, min(int(args.get("limit") or DEFAULT_RESULTS), MAX_RESULTS))
        results: list[dict[str, Any]] = []
        for hit in self.deps.search_private(ctx.account_id, query, SEARCH_POOL):
            entry = {
                "id": hit["book_id"],
                "title": hit["title"],
                "author": hit.get("author") or "Unknown",
                "passage": hit["passage"],
            }
            # Namespaced, not the bare book_id: `ctx.seen` also holds
            # catalogue books keyed by `main.BOOK_BY_ID`'s id space, and
            # nothing structurally guarantees the two id spaces never
            # collide. A string key removes the question rather than
            # relying on today's ranges happening not to overlap.
            ctx.seen[f"upload:{entry['id']}"] = entry
            results.append(entry)
            if len(results) >= limit:
                break
        return {"logged_in": True, "results": results, "count": len(results)}

    def _run_tool(self, name: str, args: Any, ctx: "_Context") -> dict[str, Any]:
        handlers = {
            "search_catalog": self._tool_search,
            "check_availability": self._tool_availability,
            "check_user_library": self._tool_library,
            "get_reading_profile": self._tool_profile,
            "search_my_library": self._tool_private_library,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        if not isinstance(args, dict):
            return {"error": "arguments must be an object"}
        try:
            return handler(args, ctx)
        except (TypeError, ValueError, KeyError) as exc:
            # A malformed call from the model is the model's mistake to
            # correct on the next step, not an outage.
            return {"error": f"bad arguments: {exc}"}

    # -- the loop ------------------------------------------------------------

    def answer(
        self,
        message: str,
        *,
        account_id: Optional[int] = None,
        profile: Any = None,
        history: Optional[list[dict[str, str]]] = None,
        prior_book_ids: Optional[list[int]] = None,
    ) -> LibrarianResult:
        ctx = _Context(account_id=account_id, profile=profile)
        # Books a tool returned in an earlier turn of this same conversation.
        # Without this, "who wrote the first one?" is rejected as invented:
        # the title is real and was grounded when it was produced, but no tool
        # returned it *this* turn. Found by a live multi-turn probe, not by
        # reasoning about it — the first three follow-ups all came back as
        # "I couldn't find anything in the catalogue matching that."
        for book_id in (prior_book_ids or []):
            book = self.deps.book_by_id(book_id)
            if book is not None:
                ctx.seen[book["id"]] = book
        # Prior turns go between the system prompt and the new question, as
        # plain user/assistant text. Tool calls and their results are
        # deliberately not replayed: they are this turn's working, they can be
        # long, and a stale search result read as current is exactly the kind
        # of ungrounded fact the guards exist to stop.
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history or [])
        messages.append({"role": "user", "content": message})

        text, model, steps = "", "", 0
        for steps in range(1, MAX_STEPS + 1):
            response = self.llm.chat(messages, tools=TOOLS)
            model = response.model or model
            if not response.tool_calls:
                text = _clean(response.content)
                break
            turn = {
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {"function": {"name": c.name, "arguments": c.arguments}}
                    for c in response.tool_calls
                ],
            }
            # Opaque provider state for this turn — Claude's thinking blocks
            # and real tool_use ids, which must go back unchanged for the
            # loop to continue (see `LLMResponse.native`). Added only when a
            # provider set it, so the Ollama request body is byte-identical
            # to before.
            if response.native is not None:
                turn["native"] = response.native
            messages.append(turn)
            for call in response.tool_calls:
                result = self._run_tool(call.name, call.arguments, ctx)
                ctx.corpus.append(json.dumps(result, ensure_ascii=False))
                log.info(f"librarian tool {call.name}({call.arguments}) -> {str(result)[:160]}")
                messages.append({
                    "role": "tool",
                    "tool_name": call.name,
                    "content": json.dumps(result, ensure_ascii=False),
                })
        else:
            # Out of steps while still calling tools. Ask once more with no
            # tools on offer, so the loop always ends in prose.
            final = self.llm.chat(messages)
            model, text = final.model or model, _clean(final.content)

        seen = list(ctx.seen.values())
        problem = None
        if text:
            title_problem = _grounding_problem(text, [b["title"] for b in seen], message)
            if title_problem:
                problem = f"quoted text no tool returned: {title_problem!r}"
            else:
                problem = _facts_problem(
                    text, message, "\n".join(ctx.corpus), ctx.prices, ctx.availability_checked
                )
        if problem or not text:
            if problem:
                log.warning(f"librarian answer rejected as ungrounded: {problem}")
            books = seen[:5]
            return LibrarianResult(_template_answer(books), books, model, steps, grounded=False)

        # Cards: only catalogue rows a tool returned, and only ones the prose
        # actually mentions — a caption and its cards should agree.
        norm_text = _norm(text)
        mentioned = [b for b in seen if _norm(b["title"]) and _norm(b["title"]) in norm_text]
        return LibrarianResult(text, mentioned, model, steps, grounded=True)


@dataclass
class _Context:
    account_id: Optional[int]
    profile: Any
    # int keys: catalogue books (main.BOOK_BY_ID's id space). Str keys
    # ("upload:<book_id>"): the reader's own private uploads — namespaced so
    # the two id spaces can never collide in one dict.
    seen: dict[int | str, dict[str, Any]] = field(default_factory=dict)
    corpus: list[str] = field(default_factory=list)   # every tool result, as text
    prices: set[float] = field(default_factory=set)   # what check_availability returned
    availability_checked: bool = False


_PLACEHOLDER_DESCRIPTION = "no description available"


def _summary(book: dict[str, Any], limit: int = 280) -> str:
    """A real description, trimmed — or nothing. The catalogue's placeholder
    string is not information, and handing it to the model invites it to
    describe a book it knows nothing about."""
    text = str(book.get("description") or "").strip()
    if not text or text.lower().startswith(_PLACEHOLDER_DESCRIPTION):
        return ""
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _language_ok(book: dict[str, Any], wanted: str) -> bool:
    """Same rule as F-47's `language_mask`: exclude only a *confirmed* other
    language. A book with no language on record is not evidence of anything."""
    if wanted in ("", "any"):
        return True
    lang = str(book.get("language", "")).strip().lower()
    if lang in ("", "unknown"):
        return True
    return lang.startswith(wanted) or (wanted == "en" and lang == "english")


def default_deps() -> LibrarianDeps:
    """The real wiring. `main` and the search stack are imported at call time,
    not module load: `main` is the live module state (B-4) and search must
    degrade rather than cascade (section 12)."""

    def search(query: str, pool: int) -> list[dict[str, Any]]:
        from api.search import _get_search_encoder
        from db import SessionLocal
        from services.search import search_books

        encoder = _get_search_encoder()
        if encoder is None:
            # Without search the librarian has nothing to ground on, so the
            # whole tier is unavailable: reuse the one exception ChatbotEngine
            # already turns into its classifier fallback (section 12).
            raise LLMUnavailable("catalogue search is not configured")
        vector = encoder.encode([query])[0]
        with SessionLocal() as session:
            hits = search_books(
                session, vector, limit=pool, embedding_model=encoder.name, min_similarity=0.0
            )
        return [{"pk": h["book_id"], "similarity": h["similarity"]} for h in hits]

    def book_for_pk(pk: int) -> Optional[dict[str, Any]]:
        import main

        idx = main.BOOK_IDX_BY_PK.get(pk)
        return main.BOOKS[idx] if idx is not None and idx < len(main.BOOKS) else None

    def book_by_id(book_id: int) -> Optional[dict[str, Any]]:
        import main

        return main.BOOK_BY_ID.get(book_id)

    def library_for(account_id: int) -> set[int]:
        import main
        from db import SessionLocal
        from models import Comment
        from services import store
        from sqlalchemy import select

        pks: set[int] = set()
        with SessionLocal() as session:
            pks.update(r.book_id for r in store.progress_for_user(session, account_id))
            pks.update(session.scalars(select(Comment.book_id).where(Comment.user_id == account_id)))
        ids = set()
        for pk in pks:
            idx = main.BOOK_IDX_BY_PK.get(pk)
            if idx is not None:
                ids.add(idx + 1)
        return ids

    def search_private(account_id: int, query: str, pool: int) -> list[dict[str, Any]]:
        """Phase 4, section 29/31. `search_chunks(user_id=account_id)`
        returns the public catalogue *and* this account's own private
        chunks together (`services.search.visible_chunks`) — correct for
        the endpoint it was built for, wrong here: `search_catalog` already
        covers the shared side, and mixing the two would blur exactly the
        distinction the system prompt asks the model to keep. Filtered to
        this account's private rows rather than adding a second retrieval
        function for one already-correct query with a different filter.
        """
        from api.search import _get_search_encoder
        from db import SessionLocal
        from services.search import search_chunks

        encoder = _get_search_encoder()
        if encoder is None:
            raise LLMUnavailable("catalogue search is not configured")
        vector = encoder.encode([query])[0]
        with SessionLocal() as session:
            hits = search_chunks(
                session, vector, user_id=account_id, limit=pool,
                embedding_model=encoder.name, min_similarity=0.0,
            )
        # `visible_chunks(account_id)` (services.search) is `public OR
        # BookChunk.user_id == account_id` — the only way a row can carry
        # `visibility == "private"` and still be in `hits` at all is if it
        # is already this account's own, so filtering on visibility alone
        # is both correct and sufficient (SearchHit does not carry user_id
        # to check redundantly).
        return [
            {
                "book_id": h.book_id, "title": h.title, "author": h.author,
                "passage": h.passage, "similarity": h.similarity,
            }
            for h in hits
            if h.visibility == "private"
        ]

    return LibrarianDeps(search, book_for_pk, book_by_id, library_for, search_private)
