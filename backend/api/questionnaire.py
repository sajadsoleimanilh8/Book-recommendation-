"""Cold-start questionnaire — POST /questionnaire.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
QUESTIONER, error_response, BOOKS, get_profile and RECOMMENDER become
main.<name>, the established transformation for main.py's live module
state (RESTRUCTURE-NOTES B-4, 5.2). The questionnaire flow is
behaviour-frozen (README rule) — this move changes nothing about it.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

import main
from services.catalogue import infer_mood
from schemas.questionnaire import QuestionnaireRequest

router = APIRouter()


@router.post("/questionnaire")
@router.post("/api/questionnaire")
def questionnaire(payload: QuestionnaireRequest) -> Dict[str, Any]:
    if not main.QUESTIONER:
        return main.error_response("Questionnaire engine not ready — ML engine still loading.")

    answers = {
        "genre": (payload.genre or "").strip().lower(),
        "mood": (payload.mood or "").strip().lower(),
        "pace": (payload.pace or "any").strip().lower(),
        "language": (payload.language or "any").strip().lower(),
        "popularity": (payload.popularity or "any").strip().lower(),
        "favorite_author": (payload.favorite_author or "").strip(),
    }

    results = main.QUESTIONER.recommend_from_answers(answers, n=payload.top_k)

    enriched = []
    for r in results:
        match = next((b for b in main.BOOKS if b["title"].lower() == r["title"].lower()), None)
        enriched.append({
            **r,
            "id": match["id"] if match else None,
            "thumbnail": match["thumbnail"] if match else "",
            "price": match["price"] if match else r.get("list_price", 0),
            "audiobook": match["audiobook"] if match else r.get("page_count", 0) > 350,
            "mood": match["mood"] if match else infer_mood(
                r.get("genre", ""),
                r.get("title", ""),
                r.get("description", "")
            ),
        })

    profile = main.get_profile(payload.user_id)
    if answers.get("mood"):
        profile.mood = answers["mood"]
    if answers.get("genre"):
        profile.preferred_genres = [answers["genre"].capitalize()]
    if answers.get("favorite_author"):
        profile.preferred_authors = [answers["favorite_author"]]

    for b in enriched:
        t = b.get("title")
        if t and t not in profile.viewed_books:
            profile.viewed_books.append(t)

    return {
        "items": enriched,
        "total": len(enriched),
        "answers": answers,
        "ml_powered": main.RECOMMENDER is not None and getattr(main.RECOMMENDER, '_fitted', False),
    }
