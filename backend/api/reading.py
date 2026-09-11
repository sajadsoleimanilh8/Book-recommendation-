"""Reading progress and reminders — POST/GET /reminder*, /progress*.

Extracted verbatim from `main.py` in the Phase D restructure, from two
non-adjacent locations: set_reminder, get_reminders, progress and the
two payload helpers sat together; get_progress_route and
get_reminders_by_params (the two GET /api/progress and /api/reminders
duplicates) sat far below, after profile and clusters. All five share
the reading domain, so they join here regardless of where main.py had
them.

Bare RECOMMENDER, error_response, BOOK_BY_ID, get_profile,
BOOK_IDX_BY_PK and BOOKS become main.<name> — main.py's live module
state (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, status

import events
import main
import store
from auth import CurrentUser, SessionDep
from schemas.reading import ProgressRequest, ReminderRequest

router = APIRouter()


@router.post("/reminder")
@router.post("/api/reminder")
def set_reminder(payload: ReminderRequest, user: CurrentUser, session: SessionDep):
    """F-07 + F-12: authenticated, and persisted in Postgres."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'reminder', None):
        return main.error_response("Reminder engine not ready.")

    b = main.BOOK_BY_ID.get(payload.book_id)
    if not b:
        return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

    book_pk = store.resolve_book_pk(session, b)
    if book_pk is None:
        return main.error_response(
            "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
        )

    try:
        reminder = main.RECOMMENDER.reminder.set_reminder(
            user_id=str(user.id),
            book_id=payload.book_id,
            title=b["title"],
            enabled=payload.enabled,
        )
    except Exception as e:
        return main.error_response(f"Reminder error: {str(e)}")

    store.set_reminder(
        session, user_id=user.id, book_pk=book_pk, enabled=payload.enabled
    )

    if reminder is None:
        return {"ok": True, "message": f"Reminder removed for '{b['title']}'"}

    return {
        "ok": True,
        "message": f"Reminder set for '{b['title']}'",
        "reminder": {
            "book_id": reminder.book_id,
            "title": reminder.title,
            "created_at": reminder.created_at,
        },
    }

@router.get("/reminder/{user_id}")
@router.get("/api/reminder/{user_id}")
def get_reminders(user_id: str, user: CurrentUser, session: SessionDep):
    """F-07 read-authz sweep: own reminders only."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'reminder', None):
        return main.error_response("Reminder engine not ready.")

    if user_id != str(user.id):
        return main.error_response(
            "You can only view your own reminders.", status.HTTP_403_FORBIDDEN
        )

    return {"user_id": user_id, "reminders": _reminder_payload(session, user)}

@router.post("/progress")
@router.post("/api/progress")
def progress(payload: ProgressRequest, user: CurrentUser, session: SessionDep) -> Dict[str, Any]:
    """F-12: reading progress persists in Postgres.
    F-07: the owner is the token holder, not a client-supplied string."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'reminder', None):
        return main.error_response("Progress tracking not ready.")

    owner_id = str(user.id)
    profile = main.get_profile(owner_id)

    if payload.book_id is not None and payload.progress is not None:
        b = main.BOOK_BY_ID.get(payload.book_id)
        if not b:
            return main.error_response("Book not found", status.HTTP_404_NOT_FOUND)

        book_pk = store.resolve_book_pk(session, b)
        if book_pk is None:
            return main.error_response(
                "Book is not in the persistent catalogue.", status.HTTP_404_NOT_FOUND
            )

        try:
            result = main.RECOMMENDER.reminder.update_progress(
                user_id=owner_id,
                book_id=payload.book_id,
                progress=payload.progress,
                total_pages=payload.total_pages or b.get("pages", 0),
                profile=profile,
            )
        except Exception as e:
            return main.error_response(f"Progress update error: {str(e)}")

        store.save_progress(
            session,
            user_id=user.id,
            book_pk=book_pk,
            progress=payload.progress,
            page=int(payload.total_pages or 0),
        )
        # F-42. Section 24 lists chapter completion and abandonment as
        # behavioural signals; both are read off a series of these. A single
        # progress row says how far someone got, but the *sequence* says
        # whether they finished or gave up, which is the part that matters.
        events.record(
            events.PROGRESS,
            user_id=user.id,
            book_id=book_pk,
            context={
                "progress": payload.progress,
                "total_pages": payload.total_pages,
            },
        )
        return result

    return _progress_payload(session, user)


def _reminder_payload(session, user) -> List[Dict[str, Any]]:
    """Read reminders from Postgres, mapped back to positional book ids."""
    out = []
    for row in store.reminders_for_user(session, user.id):
        book_idx = main.BOOK_IDX_BY_PK.get(row.book_id)
        book = main.BOOKS[book_idx] if book_idx is not None and book_idx < len(main.BOOKS) else None
        out.append(
            {
                "book_id": (book_idx + 1) if book_idx is not None else None,
                "title": book["title"] if book else None,
                "enabled": row.enabled,
                "last_notified": row.last_notified,
                "created_at": row.created_at.isoformat(),
            }
        )
    return out


def _progress_payload(session, user) -> Dict[str, Any]:
    """Read progress from Postgres, mapped back to the API's positional ids."""
    items = []
    for row in store.progress_for_user(session, user.id):
        book_idx = main.BOOK_IDX_BY_PK.get(row.book_id)
        items.append(
            {
                # book_idx is the DataFrame row; the API exposes id = idx + 1.
                "book_id": (book_idx + 1) if book_idx is not None else None,
                "progress": row.progress,
                "percent": int(row.progress * 100),
                "page": row.page,
                "updated_at": row.updated_at.isoformat(),
            }
        )
    return {"user_id": str(user.id), "items": items, "total": len(items)}


@router.get("/api/progress")
def get_progress_route(user: CurrentUser, session: SessionDep):
    """F-07: user_id was a query parameter, so anyone could read anyone's
    reading history. It is now the token holder, and unreadable otherwise."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'reminder', None):
        return main.error_response("Progress tracking not ready.")

    return _progress_payload(session, user)


# Characters per rendered page. ~1,800 is a mass-market paperback page.
@router.get("/api/reminders")
def get_reminders_by_params(user: CurrentUser, session: SessionDep, book_id: Optional[int] = None):
    """F-07 read-authz sweep: user_id was a query parameter defaulting to
    "guest", so anyone could enumerate anyone's reminders. It is now the
    token holder.
    F-12: read from Postgres."""
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'reminder', None):
        return main.error_response("Reminder engine not ready.")

    reminders = _reminder_payload(session, user)
    user_id = str(user.id)

    if book_id is not None:
        reminders = [r for r in reminders if r.get("book_id") == book_id]
    
    return {
        "user_id": user_id,
        "reminders": reminders,
        "total": len(reminders)
    }
