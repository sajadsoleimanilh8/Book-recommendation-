"""Chatbot — POST /chatbot, /api/chat.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
RECOMMENDER, error_response and get_profile become main.<name> —
main.py's live module state (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

from fastapi import APIRouter

import main
from auth import OptionalUser
from core import conversations
from schemas.chat import ChatbotRequest

router = APIRouter()


@router.post("/chatbot")
@router.post("/api/chat")
def chatbot(payload: ChatbotRequest, user: OptionalUser):
    if not main.RECOMMENDER or not getattr(main.RECOMMENDER, 'chatbot', None):
        return main.error_response("Chatbot engine not ready.")

    profile = main.get_profile(payload.user_id)
    # The real account, never `payload.user_id`: that is a free-text profile
    # key anyone can send, and both the librarian's library tool and the
    # conversation history below read private data (F-07).
    account_id = user.id if user else None
    # F-22 Phase D. A signed-in caller is keyed on their account and the
    # supplied conversation_id is ignored; an anonymous one keeps the opaque
    # token minted for them. See core.conversations.resolve_key.
    history_key, conversation_id = conversations.resolve_key(
        account_id, payload.conversation_id
    )
    stored = conversations.load(history_key)
    # The model sees prose only; `book_ids` rides along so this turn knows
    # which books an earlier answer was grounded in.
    history = [{"role": t["role"], "content": t["content"]} for t in stored]
    prior_book_ids = [i for t in stored for i in (t.get("book_ids") or [])]

    try:
        response = main.RECOMMENDER.chatbot.respond(
            user_message=payload.message,
            user_id=payload.user_id,
            profile=profile,
            book_idx=payload.book_id,
            account_id=account_id,
            history=history,
            prior_book_ids=prior_book_ids,
        )
    except Exception as e:
        return main.error_response(f"Chatbot error: {str(e)}")

    answer = response.get("message", "")
    conversations.append(history_key, [
        {"role": "user", "content": payload.message},
        {
            "role": "assistant",
            "content": answer,
            "book_ids": [b["id"] for b in response.get("books", []) if b.get("id")],
        },
    ])

    return {
        **response,
        "answer": answer,
        "recommendations": response.get("books", [])[:8],
        "ml_powered": True,
        # Echoed back so an anonymous caller can continue the same thread.
        # Empty for a signed-in one: their account is the key, and handing
        # them a token to send would be a second, weaker way to say who they
        # are.
        "conversation_id": conversation_id,
    }

