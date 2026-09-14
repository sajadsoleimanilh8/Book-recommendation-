"""Filtering and recommendations — POST /filter, /recommend.

Extracted verbatim from `main.py` in the Phase D restructure. The
established transformation: bare BOOKS, get_profile, RECOMMENDER and
MODEL_VERSION become main.<name> — live module state or a constant
defined after this router's import line in main.py, both only safe to
reach via `import main` at call time (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter

import events
import main
from engine import GutenbergClient
from schemas.recommend import FilterRequest, RecommendRequest
from services.catalogue import _safe_float, _safe_int, price_within
from services.recommendation import _gutenberg_to_api, _ml_to_api, apply_filters, fuse_and_rank

router = APIRouter()


@router.post("/filter")
@router.post("/api/filter")
def filter_books(payload: FilterRequest) -> Dict[str, Any]:
    items = apply_filters(
        main.BOOKS,
        genre=payload.genre,
        rating_min=payload.rating_min,
        language=payload.language,
        mood=payload.mood,
        max_price=payload.max_price
    )
    items = sorted(
        items,
        key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
        reverse=True
    )
    return {"items": items, "total": len(items)}

@router.post("/recommend")
@router.post("/api/recommend")
def recommend(payload: RecommendRequest) -> Dict[str, Any]:
    started = time.perf_counter()
    profile = main.get_profile(payload.user_id)

    # F-47. Unconditional, unlike the fields below: RecommendRequest.language
    # always carries a real value ("en" by default, "any" to opt out), so
    # there is no "the caller didn't mention it" case to preserve a cached
    # profile's prior value for, the way there is for mood/genre/author.
    profile.language = payload.language

    if payload.feeling:
        profile.mood = payload.feeling.lower().strip()
    if payload.genre:
        profile.preferred_genres = [g.strip() for g in payload.genre.split(",")]
    if payload.favorite_author:
        profile.preferred_authors = [payload.favorite_author.strip()]

    local_results: List[Dict[str, Any]] = []

    if main.RECOMMENDER and getattr(main.RECOMMENDER, '_fitted', False):
        if payload.favorite_book:
            ml_books = main.RECOMMENDER.recommend_by_book(payload.favorite_book, profile, n=payload.top_k * 2)
        else:
            ml_books = main.RECOMMENDER.recommend_by_profile(profile, n=payload.top_k * 2)

        filtered_ml_books = [
            b for b in ml_books
            if (
                price_within(b, payload.max_price) and
                (payload.min_rating is None or _safe_float(b.get("average_rating")) >= payload.min_rating) and
                (payload.min_pages is None or _safe_int(b.get("page_count")) >= payload.min_pages) and
                (payload.max_pages is None or _safe_int(b.get("page_count")) <= payload.max_pages)
            )
        ]

        local_results = [_ml_to_api(b, i + 1) for i, b in enumerate(filtered_ml_books)]
    else:
        items = apply_filters(
            main.BOOKS,
            genre=payload.genre,
            max_price=payload.max_price,
            mood=payload.feeling
        )
        items = sorted(
            items,
            key=lambda b: (_safe_float(b.get("rating")), _safe_int(b.get("ratings_count"))),
            reverse=True
        )[:payload.top_k]
        local_results = items

    g_query = payload.genre or payload.feeling or payload.favorite_book or "classic"
    try:
        g_books = [_gutenberg_to_api(g, i + 1) for i, g in enumerate(GutenbergClient.search(g_query))]
    except Exception:
        g_books = []

    final = fuse_and_rank(local_results, g_books, top_n=payload.top_k)

    for b in final:
        if b.get("title") and b["title"] not in profile.viewed_books:
            profile.viewed_books.append(b["title"])

    # F-42. This is the row F-13 is actually waiting on. A click recorded
    # later means nothing unless we know what was *shown* alongside it — the
    # books that were offered and passed over are half the training signal,
    # and they exist nowhere else.
    #
    # `shown` stores ids and ranks only. Storing the rendered payload would
    # duplicate the catalogue into an append-only table and rot as it changes.
    events.record_recommendation(
        user_id=None,  # RecommendRequest.user_id is a client string, not a row
        request={
            "profile_user": payload.user_id,
            "genre": payload.genre,
            "feeling": payload.feeling,
            "favorite_book": payload.favorite_book,
            "favorite_author": payload.favorite_author,
            "top_k": payload.top_k,
            "filters": {
                "max_price": payload.max_price,
                "min_rating": payload.min_rating,
                "min_pages": payload.min_pages,
                "max_pages": payload.max_pages,
            },
        },
        shown={
            "book_ids": [b.get("id") for b in final],
            "ranks": list(range(1, len(final) + 1)),
        },
        model_version=main.MODEL_VERSION,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )

    return {"items": final, "total": len(final), "ml_powered": main.RECOMMENDER is not None}

