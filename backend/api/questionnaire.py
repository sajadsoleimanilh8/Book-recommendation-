"""Cold-start questionnaire — POST /questionnaire.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
QUESTIONER, error_response, BOOKS, get_profile and RECOMMENDER become
main.<name>, the established transformation for main.py's live module
state (RESTRUCTURE-NOTES B-4, 5.2). The questionnaire flow is
behaviour-frozen (README rule) — this move changes nothing about it.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter

import main
from domain.entities import MOOD_GENRE_MAP
from services.catalogue import infer_mood
from schemas.questionnaire import QuestionnaireRequest

router = APIRouter()


def _question(id_: str, text: str, options: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"id": id_, "text": text, "options": options or []}


@router.get("/questionnaire/options")
@router.get("/api/questionnaire/options")
def questionnaire_options() -> Dict[str, Any]:
    """The question set — F-50.

    `frontend/js/questionnair.js` has always fetched this path; it 404'd on
    every load and fell back to its own hardcoded question set silently (a
    try/catch around the fetch), which is why nobody noticed. That fallback
    is not being touched — this endpoint replaces the need for it, it does
    not patch it.

    The values below are not free-form UI copy: `mood`, `pace`, `language`
    and `popularity` are matched against exact vocabularies downstream
    (`QuestionerEngine.MOOD_NORMALISE`, `.PACE_PAGE_MAP`, and the literal
    "popular"/"underrated" checks in `_filter_df`, plus the language column's
    own two-letter codes). Sending anything else silently falls through to
    "any" rather than erroring, so a drifted option here would be a silent
    no-op, not a loud failure — the same shape of bug F-16 already cost this
    project once. `mood` is therefore built directly from
    `MOOD_GENRE_MAP`'s own keys rather than a second, hand-written list that
    could drift from it.

    `genre` is different: `_filter_df` matches it with a case-insensitive
    substring search, so any real genre string works. Offered here are the
    ten most common non-"Unknown" genres in the live catalogue, rather than
    a guessed list that might not exist in the data at all.
    """
    genre_counts = Counter(
        g
        for b in main.BOOKS
        if (g := str(b.get("genre", "")).strip()) and g.lower() != "unknown"
    )
    top_genres = [g for g, _ in genre_counts.most_common(10)]

    return {
        "questions": [
            _question("genre", "What genre do you prefer?", top_genres + ["any"]),
            _question("mood", "What mood are you in?", list(MOOD_GENRE_MAP) + ["any"]),
            _question("pace", "What reading pace do you prefer?", ["fast", "slow", "any"]),
            _question("language", "Preferred language?", ["en", "fa", "fr", "de", "es", "any"]),
            _question("popularity", "Popular or underrated?", ["popular", "underrated", "any"]),
            _question("favorite_author", "Favorite author (optional)"),
        ]
    }


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
