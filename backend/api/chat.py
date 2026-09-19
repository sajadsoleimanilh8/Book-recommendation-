"""Chatbot — POST /chatbot, /api/chat.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
RECOMMENDER, error_response and get_profile become main.<name> —
main.py's live module state (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

from fastapi import APIRouter

import main
from auth import OptionalUser
from schemas.chat import ChatbotRequest

router = APIRouter()


@router.post("/chatbot")
@router.post("/api/chat")
def chatbot(payload: ChatbotRequest, user: OptionalUser):
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'chatbot', None):
        return main.error_response("Chatbot engine not ready.")

    profile = main.get_profile(payload.user_id)
    try:
        response = main.RECOMMENDER.chatbot.respond(
            user_message=payload.message,
            user_id=payload.user_id,
            profile=profile,
            book_idx=payload.book_id,
            # The real account, never `payload.user_id`: that is a free-text
            # profile key anyone can send, and the librarian's library tool
            # reads private reading history (F-07).
            account_id=user.id if user else None,
        )
    except Exception as e:
        return main.error_response(f"Chatbot error: {str(e)}")

    return {
        **response,
        "answer": response.get("message", ""),
        "recommendations": response.get("books", [])[:8],
        "ml_powered": True,
    }

