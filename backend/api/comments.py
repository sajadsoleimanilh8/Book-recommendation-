"""Comments — POST/GET/DELETE /comments*, plus the books-domain shortcut.

Extracted verbatim from `main.py` in the Phase D restructure: add_comment,
get_comments, delete_comment, and GET /api/books/{book_id}/comments — the
guaranteed-500 shortcut (B-1) deferred out of the books router extraction
(Phase D6) because it calls get_comments without the required `session`
parameter. That bug is preserved exactly, not fixed: the call is now a
plain same-module reference to the function below it, same missing
argument, same TypeError, same 500 it has always been.

Bare RECOMMENDER, error_response, BOOK_BY_ID, get_profile and
BOOK_IDX_BY_PK become main.<name> — main.py's live module state
(RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status

import events
import main
import models
import store
from auth import CurrentUser, SessionDep
from schemas.comments import CommentRequest

log = logging.getLogger(__name__)

router = APIRouter()


@router.post("/comments")
@router.post("/api/comments")
def add_comment(payload: CommentRequest, user: CurrentUser, session: SessionDep):
    """F-07: authenticated, owner is the token holder.
    F-12: written through to Postgres before responding."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'comment', None):
        return main.error_response("Comment engine not ready.")

    b = main.BOOK_BY_ID.get(payload.book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    if book_pk is None:
        return main.error_response(
            "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
        )

    # F-12: was DF[DF["title"] == b["title"]].index[0], which attached the
    # comment to the first book sharing a title. Positional arithmetic is
    # exact — see store.df_index_for.
    book_idx = store.df_index_for(b)
    if not (0 <= book_idx < len(main.RECOMMENDER.df)):
        return main.error_response("Book not in ML index.")

    owner_id = str(user.id)
    profile = main.get_profile(owner_id)

    try:
        # Updates comment_score on the in-memory DataFrame — the
        # ranking-facing cache.
        comment = main.RECOMMENDER.comment.add(
            book_idx=book_idx,
            user_id=owner_id,
            text=payload.comment,
            rating=payload.rating,
            profile=profile,
        )
    except Exception as e:
        return main.internal_error("Comment error", e)

    row = store.save_comment(
        session,
        user_id=user.id,
        book_pk=book_pk,
        text=comment.text,
        rating=comment.rating,
        sentiment=comment.sentiment,
        keywords=list(comment.keywords or []),
    )

    # F-42. The comment itself is already durable; this records it as an
    # *interaction* so it sits in the same series as views, searches and
    # progress. F-26 found the comment loop is currently decorative — a
    # maxed comment_score moves no ranking — and this is the row that makes
    # a real fix measurable rather than asserted.
    events.record(
        events.COMMENT,
        user_id=user.id,
        book_id=book_pk,
        context={"rating": comment.rating, "sentiment": comment.sentiment},
    )

    return {
        "ok": True,
        "comment": {
            "id": row.id,
            "user_id": comment.user_id,
            "text": comment.text,
            "rating": comment.rating,
            "sentiment": comment.sentiment,
            "keywords": comment.keywords,
            "timestamp": comment.timestamp,
        },
        "book_comment_score": round(float(main.RECOMMENDER.df.loc[book_idx, "comment_score"]), 3),
    }

@router.get("/comments/{book_id}")
@router.get("/api/comments/{book_id}")
def get_comments(book_id: int, session: SessionDep):
    """F-12: comments are read from Postgres, the source of truth. The
    in-memory store is only the ranking-facing cache now."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'comment', None):
        return main.error_response("Comment engine not ready.")

    b = main.BOOK_BY_ID.get(book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_idx = store.df_index_for(b)
    if not (0 <= book_idx < len(main.RECOMMENDER.df)):
        return main.error_response("Book not in ML index.")

    book_pk = store.resolve_book_pk(session, b)
    rows = store.comments_for_book(session, book_pk) if book_pk is not None else []

    return {
        "book_id": book_id,
        "book": {"title": b["title"], "author": b["author"]},
        "comments": [
            {
                "id": c.id,
                "user_id": str(c.user_id),
                "text": c.text,
                "rating": c.rating,
                "sentiment": c.sentiment,
                "keywords": c.keywords or [],
                "timestamp": c.created_at.isoformat(),
            }
            for c in rows
        ],
        "summary": main.RECOMMENDER.comment.summary(book_idx),
    }

@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: int, user: CurrentUser, session: SessionDep):
    """Delete one's own comment, by durable id.

    F-07: previously unauthenticated with no ownership check — any caller
    could delete any comment on any book.

    F-12 / contract change: the path was
    `/api/comments/{book_id}/{comment_index}`. Positional indices are racy
    once comments are shared, persistent rows: two concurrent deletes shift
    each other's target and remove the wrong comment. Comments now carry a
    stable id, returned by POST and by GET /api/comments/{book_id}.
    """
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'comment', None):
        return main.error_response("Comment engine not ready.")

    row = session.get(models.Comment, comment_id)
    if row is None:
        return main.error_response("Comment not found.", status.HTTP_404_NOT_FOUND)

    if row.user_id != user.id:
        # 403, not 404: the caller has proven identity, and the comment is
        # readable via GET anyway, so hiding its existence buys nothing.
        return main.error_response(
            "You can only delete your own comments.", status.HTTP_403_FORBIDDEN
        )

    book_pk = row.book_id
    # Position within this book's comments, in the same insertion order the
    # in-memory cache is built in, so the cache stays aligned.
    ordered = store.comments_for_book(session, book_pk)
    position = next((i for i, c in enumerate(ordered) if c.id == comment_id), None)

    if not store.delete_comment(session, comment_id=comment_id, user_id=user.id):
        return main.error_response("Comment not found.", status.HTTP_404_NOT_FOUND)

    # Keep the ranking-facing cache and comment_score in step.
    removed_text, removed_sentiment = row.text, row.sentiment
    book_idx = main.BOOK_IDX_BY_PK.get(book_pk)
    if book_idx is not None and position is not None:
        try:
            main.RECOMMENDER.comment.delete(book_idx, position)
        except Exception:  # pragma: no cover - cache drift must not 500
            log.warning(f"Comment cache out of step for book_pk={book_pk}")

    return {
        "ok": True,
        "removed": {"id": comment_id, "text": removed_text, "sentiment": removed_sentiment},
    }

@router.get("/api/books/{book_id}/comments")
def get_book_comments_shortcut(book_id: int):
    
    return get_comments(book_id)
