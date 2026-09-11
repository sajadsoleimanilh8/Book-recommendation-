"""Cluster summary — GET /api/clusters.

Extracted verbatim from `main.py` in the Phase D restructure. Bare
BOOKS becomes main.BOOKS (RESTRUCTURE-NOTES B-4, 5.2).
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

import main
from services.catalogue import _safe_float

router = APIRouter()


@router.get("/api/clusters")
def cluster_info() -> Dict[str, Any]:
    clusters: Dict[int, Any] = {}

    for b in main.BOOKS:
        c = b.get("cluster", -1)
        if c not in clusters:
            clusters[c] = {"id": c, "count": 0, "genres": {}, "avg_rating": 0.0, "sample_books": []}

        clusters[c]["count"] += 1
        g = b.get("genre", "Unknown")
        clusters[c]["genres"][g] = clusters[c]["genres"].get(g, 0) + 1
        clusters[c]["avg_rating"] += _safe_float(b.get("rating"))

        if len(clusters[c]["sample_books"]) < 3:
            clusters[c]["sample_books"].append(
                {"title": b["title"], "author": b["author"]}
            )

    for c in clusters.values():
        if c["count"] > 0:
            c["avg_rating"] = round(c["avg_rating"] / c["count"], 2)
            c["top_genre"] = max(c["genres"], key=c["genres"].get) if c["genres"] else "Unknown"

    return {"clusters": list(clusters.values()), "total_clusters": len(clusters)}
