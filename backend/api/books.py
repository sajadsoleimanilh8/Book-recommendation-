"""Catalogue browsing — GET /books, /books/{id}, filter-options, pages.

Extracted verbatim from `main.py` in the Phase D restructure, with the
same necessary transformation as every router before it: bare
references to BOOKS, BOOK_BY_ID and error_response become
`main.<name>`, because they are main.py's live module state or a
function defined after this router is registered — both only safe to
reach at call time via `import main` (RESTRUCTURE-NOTES B-4, 5.2).

GET /api/books/{book_id}/comments (the guaranteed-500 shortcut, B-1)
does NOT move here despite its path, because its body calls
get_comments, a comments-domain function that has not moved yet. It
stays in main.py until the comments router extraction, when both sides
of that call can move together.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import Query, status
from fastapi import APIRouter

import events
import main
import models
import store
from auth import OptionalUser, SessionDep
from services.catalogue import _safe_float, _safe_int, weighted_rating
from services.recommendation import apply_filters

router = APIRouter()


@router.get("/books")
@router.get("/api/books")
def get_books(
    limit: int = Query(24, ge=1, le=500),
    q: Optional[str] = None,
    genre: Optional[str] = None,
    mood: Optional[str] = None,
    language: Optional[str] = None,
    rating_min: Optional[float] = None,
    audiobook: Optional[bool] = None,
) -> Dict[str, Any]:
    items = apply_filters(
        main.BOOKS,
        genre=genre,
        rating_min=rating_min,
        language=language,
        q=q,
        mood=mood,
        audiobook=audiobook,
    )
    # F-67. The old key was `(rating, ratings_count)` on the raw average,
    # which put 251 rows holding 5.0 from a *single* rating above a book with
    # 4,780,653 ratings at 4.5 — and ranked the 19,342 books carrying a
    # fabricated rating as though it were real.
    #
    # `weighted_rating` shrinks towards the catalogue mean by how much
    # evidence there is, so a lone 5.0 lands at 4.03 and an established book
    # barely moves. Unrated books sort last rather than being dropped: this
    # is a catalogue listing, and a book with no ratings is still a book.
    # `ratings_count` stays as the tiebreak among books of equal standing.
    items = sorted(
        items,
        key=lambda b: (
            weighted_rating(b) is not None,
            weighted_rating(b) or 0.0,
            _safe_int(b.get("ratings_count")),
        ),
        reverse=True,
    )
    return {"items": items[:limit], "total": len(items)}

# F-16: /api/books/filter-options MUST stay above /api/books/{book_id}.
# FastAPI matches in declaration order, so with the catch-all first the
# literal path was captured by it and int("filter-options") failed with 422 —
# the endpoint was unreachable. Do not reorder these two.
@router.get("/api/books/filter-options")
def filter_options() -> Dict[str, Any]:
    def uniq(key: str) -> List[str]:
        return sorted({str(b.get(key, "")).strip() for b in main.BOOKS if b.get(key)})[:200]

    return {
        "genres": uniq("genre"),
        "authors": uniq("author"),
        "moods": uniq("mood"),
        "languages": uniq("language"),
        "clusters": sorted({b.get("cluster", -1) for b in main.BOOKS}),
    }


@router.get("/books/{book_id}")
@router.get("/api/books/{book_id}")
def book_detail(book_id: int, user: OptionalUser, session: SessionDep):
    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)
    # F-42. A book view is the cheapest real interest signal there is, and it
    # is the one a ranking model needs most: ratings tell you what people
    # finished, views tell you what they considered.
    events.record(
        events.BOOK_VIEW,
        user_id=user.id if user else None,
        book_id=store.resolve_book_pk(session, b),
        context={"genre": b.get("genre"), "source": b.get("source")},
    )
    return b


PAGE_CHARS = 1800


def _record_page_turn(
    book_pk: Optional[int],
    page: int,
    page_size: int,
    *,
    excerpt_pages: int,
    had_content: bool,
    user_id: Optional[int] = None,
) -> None:
    """One reading-page event — the raw material of F-26's relevance target.

    `user_id` is optional, the same shape as `book_detail`'s `BOOK_VIEW`
    event above: a logged-in reader's page turns are now attributed to them
    (OI-6 follow-through), an anonymous one still records `None` — anonymous
    is signal, not noise (`interaction_events.user_id` is nullable on
    purpose). `page_size` is kept so aggregation can tell a genuine page turn
    (the reader asks for 1) from a bulk fetch (the default 24).

    Never raises — `events.record` swallows its own failures, so a logging
    outage cannot break reading (F-42).
    """
    events.record(
        events.READING_PAGE,
        user_id=user_id,
        book_id=book_pk,
        context={
            "page": page,
            "page_size": page_size,
            "excerpt_pages": excerpt_pages,
            "had_content": had_content,
        },
    )


def _uploads_are_not_rendered(session, book_id: int, user, page: int, page_size: int):
    """Answer `/pages` for an id that is not in the catalogue — F-68, OI-4.

    Two outcomes, and the difference between them is the point:

    * **The owner** gets `text_available: False` and a reason naming the
      policy. Their book is ingested and answers questions; being told
      "Book not found" when they open it says the app lost it.
    * **Everyone else** gets exactly what a nonexistent id gets. Not a
      "forbidden", not a different message, not a different status — the
      same `error_response` with the same text, built here rather than
      phrased separately, so the two cannot drift apart. A 403 would confirm
      the book exists, which for a private upload is the thing being
      protected (§21/§22, and the structural guarantee `owned_by` gives
      elsewhere).

    No page turn is recorded either way. `_record_page_turn` exists to
    measure reading depth, and nothing is being read.
    """
    not_found = main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    if user is None:
        return not_found

    book = session.get(models.Book, book_id)
    # `owner_id` is the marker, not `source == "upload"` — the same
    # structural check the isolation tests pin.
    if book is None or book.owner_id is None or book.owner_id != user.id:
        return not_found

    return {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "text_available": False,
        "reason": (
            "This is your own upload. DigiKitab can search it and answer "
            "questions grounded in it, but it does not display its pages."
        ),
    }


@router.get("/api/books/{book_id}/pages")
def book_pages_route(
    book_id: int,
    user: OptionalUser,
    session: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    """F-17: this returned fabricated content for every page of every book —
    the literal string "page{n} از {title}". It now serves real text where we
    hold it, and says so honestly where we do not.

    Text is currently public-domain Gutenberg only (§11), and bounded to the
    opening chapters. Whole-book reading is Phase 5.

    `OptionalUser`, not `CurrentUser`: reading itself has never required an
    account (§12) and still does not — this only lets a page turn be
    attributed to whoever is reading it, when someone is logged in.
    """
    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        # Not in the catalogue. It may still be an upload, which is keyed by
        # database id and never enters `BOOK_BY_ID` — F-68 was filed as a bug
        # for that and closed as by-design: OI-4's Phase 4 extension makes
        # uploads ingest-only, so extraction, chunking, embedding and
        # retrieval are permitted and **rendering a page is not**. Nothing
        # here starts serving upload pages.
        #
        # What changes is only what the *owner* is told. A reader whose own
        # book answers questions but 404s on open cannot tell a policy from a
        # fault, so they get the same honest empty state any text-less book
        # gets, with the reason naming the policy.
        return _uploads_are_not_rendered(session, book_id, user, page, page_size)

    book_pk = store.resolve_book_pk(session, b)
    record = session.get(models.BookText, book_pk) if book_pk is not None else None
    user_id = user.id if user else None

    if record is None:
        # F-26: logged even though it is not reading. A request for a book we
        # hold no text for is a content gap someone tried to fill, and costs
        # nothing to record. had_content=False keeps it out of depth.
        _record_page_turn(
            book_pk, page, page_size, excerpt_pages=0, had_content=False, user_id=user_id
        )
        # Honest empty state rather than invented prose. Section 18's
        # principle applied to content: unknown is reported, never fabricated.
        return {
            "items": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "text_available": False,
            "reason": "No readable text is available for this book yet.",
        }

    paragraphs = record.content.split("\n\n")
    pages: List[str] = []
    buffer = ""
    for para in paragraphs:
        # Break on paragraph boundaries so a page never splits mid-sentence.
        if buffer and len(buffer) + len(para) + 2 > PAGE_CHARS:
            pages.append(buffer.strip())
            buffer = para
        else:
            buffer = f"{buffer}\n\n{para}" if buffer else para
    if buffer.strip():
        pages.append(buffer.strip())

    start = (page - 1) * page_size
    window = pages[start : start + page_size]

    # F-26. The reader pages past the end of the excerpt freely — its pager
    # uses the book's full length (`currentBook.pages || 100`), so it will ask
    # for page 300 of an 11-page excerpt. Those requests come back empty and
    # must not count as reading deeper; had_content is what excludes them.
    _record_page_turn(
        book_pk,
        page,
        page_size,
        excerpt_pages=len(pages),
        had_content=bool(window),
        user_id=user_id,
    )

    return {
        "items": [
            {"page_number": start + i + 1, "content": text, "book_id": book_id}
            for i, text in enumerate(window)
        ],
        "total": len(pages),
        "page": page,
        "page_size": page_size,
        "text_available": True,
        "text_source": record.source,
        # The stored text is the opening chapters, not the whole book. Say so,
        # so a client never presents a partial work as complete.
        "is_complete": record.is_complete,
    }


@router.get("/api/filter-options")
def filter_options_short():
    
    return filter_options()
