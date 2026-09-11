"""Regression tests for F-03 — the app must never silently serve fake data.

F-03 was invisible precisely because nothing asserted the catalogue had
loaded and /health could not express the failure. These tests are that
assertion.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
CATALOGUE = BACKEND / "site_ready_books.json"

EXPECTED_RECORDS = 29975


def load_catalogue():
    rows = []
    with CATALOGUE.open(encoding="utf-8") as f:
        if f.read(1) == "[":
            f.seek(0)
            return json.load(f)
        f.seek(0)
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def test_catalogue_exists_where_main_looks_for_it():
    """The exact failure of F-03: the file existed, the code looked elsewhere.

    DATA_FILE_CANDIDATES moved from main.py to services/catalogue.py in
    the Phase D restructure (RESTRUCTURE-NOTES 3.3) — this guard follows
    it there. What it asserts is unchanged.
    """
    src = (BACKEND / "services" / "catalogue.py").read_text(encoding="utf-8")
    candidates = src.split("DATA_FILE_CANDIDATES = [")[1].split("]")[0]
    assert 'BACKEND_DIR / "site_ready_books.json"' in candidates, (
        "backend/site_ready_books.json is not in DATA_FILE_CANDIDATES — the "
        "app will fall back to synthetic data again"
    )
    assert CATALOGUE.exists(), f"catalogue missing from {CATALOGUE}"


def test_catalogue_parses_with_expected_record_count():
    rows = load_catalogue()
    assert len(rows) == EXPECTED_RECORDS, (
        f"expected {EXPECTED_RECORDS} records, got {len(rows)}"
    )


def test_catalogue_is_real_not_synthetic():
    """Synthetic data is titled 'Book 00000' by 'Author 42'. Real data is not."""
    rows = load_catalogue()[:100]
    titles = [r.get("title", "") for r in rows]
    assert not any(t.startswith("Book 0") for t in titles), (
        "synthetic data detected in the catalogue file"
    )
    assert not any(str(r.get("author", "")).startswith("Author ") for r in rows), (
        "synthetic authors detected in the catalogue file"
    )


def test_health_can_report_synthetic():
    """The blind spot that hid F-03 for the life of the project.

    The /health body moved from main.py to api/health.py in the Phase D
    restructure (RESTRUCTURE-NOTES 3.3) — this guard follows it there.
    What it asserts is unchanged.
    """
    src = (BACKEND / "api" / "health.py").read_text(encoding="utf-8")
    assert '"synthetic"' in src, "/health cannot report a synthetic-data boot"
    assert "using_real_data" in src, "/health lost its using_real_data flag"
    assert '"ok": True,\n        "books_loaded"' not in src, (
        "/health reports ok:True unconditionally again — it must be False "
        "when serving synthetic data"
    )


def test_load_limit_does_not_silently_truncate():
    """F-20: limit=5000 quietly dropped 24975 of 29975 books.

    load_books_raw moved from main.py to services/catalogue.py in the
    Phase D restructure (RESTRUCTURE-NOTES 3.3) — this guard follows it
    there. What it asserts is unchanged.
    """
    src = (BACKEND / "services" / "catalogue.py").read_text(encoding="utf-8")
    assert "def load_books_raw(limit: Optional[int] = None)" in src, (
        "load_books_raw has a hardcoded default limit again"
    )


# --------------------------------------------------------------------------
# Catalogue quality baseline — the Phase 2 starting line (F-15)
# --------------------------------------------------------------------------

def test_missing_descriptions_baseline():
    """Documents the 0% description rate that blocks the AI roadmap.

    This test asserts the CURRENT broken state on purpose. When Phase 2
    enrichment lands it will fail, and that failure is the signal to update
    the threshold. It exists so the number cannot drift unnoticed.
    """
    rows = load_catalogue()
    usable = sum(
        1
        for r in rows
        if r.get("description") not in (None, "", "No description available")
    )
    ratio = usable / len(rows)
    assert ratio == 0.0, (
        f"description coverage is now {ratio:.1%} (was 0%). If Phase 2 "
        "enrichment has started, raise this threshold deliberately."
    )
