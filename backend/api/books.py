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
from services.catalogue import _safe_float, _safe_int
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
) -> Dict[str, Any]:
    items = apply_filters(
        main.BOOKS,
        genre=genre,
        rating_min=rating_min,
        language=language,
        q=q,
        mood=mood
    )
    items = sorted(
        items,
        key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
        reverse=True
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


@router.get("/api/books/{book_id}/pages")
def book_pages_route(
    book_id: int,
    session: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    """F-17: this returned fabricated content for every page of every book —
    the literal string "page{n} از {title}". It now serves real text where we
    hold it, and says so honestly where we do not.

    Text is currently public-domain Gutenberg only (§11), and bounded to the
    opening chapters. Whole-book reading is Phase 5.
    """
    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    record = session.get(models.BookText, book_pk) if book_pk is not None else None

    if record is None:
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
