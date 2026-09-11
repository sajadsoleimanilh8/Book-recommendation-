"""F-26's answer, part 1 — page turns must become events.

The product owner chose reading depth as the relevance target because it was
the only signal capturable with no login and no new UI: the reader already
requests one page per turn (`/pages?page=N&page_size=1`). It was simply never
logged.

These tests pin the two ways the raw signal could quietly lie:

* the pager uses the book's full length, so a reader can page past the end of
  an 11-page excerpt — those empty pages must not look like deeper reading;
* a request for a book with no text is not reading at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import events  # noqa: E402
from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")

# Three paragraphs of ~1,000 characters: the endpoint packs whole paragraphs
# into 1,800-character pages, so this yields exactly three pages.
_PARAGRAPH = ("The reader turned the page and found the story waiting. " * 18).strip()
_TEXT = "\n\n".join([_PARAGRAPH] * 3)


@pytest.fixture
def book_with_text(fitted_app):
    """A catalogue book with a known three-page excerpt, and clean events."""
    import store
    from db import SessionLocal
    from models import BookText, InteractionEvent

    main, client = fitted_app
    book = main.BOOK_BY_ID[1]

    with SessionLocal() as session:
        pk = store.resolve_book_pk(session, book)
        if pk is None:
            pytest.skip("book 1 is not in the persistent catalogue")
        existing = session.get(BookText, pk)
        original = (existing.content, existing.char_count) if existing else None
        if existing:
            existing.content, existing.char_count = _TEXT, len(_TEXT)
        else:
            session.add(BookText(book_id=pk, source="gutenberg", content=_TEXT,
                                 char_count=len(_TEXT), is_complete=False))
        session.query(InteractionEvent).delete()
        session.commit()

    yield client, pk

    with SessionLocal() as session:
        row = session.get(BookText, pk)
        if original is None:
            if row is not None:
                session.delete(row)
        else:
            row.content, row.char_count = original
        session.query(InteractionEvent).delete()
        session.commit()


def _page_events():
    from db import SessionLocal
    from models import InteractionEvent

    with SessionLocal() as session:
        return [
            e.context for e in session.query(InteractionEvent)
            .filter(InteractionEvent.event_type == events.READING_PAGE)
            .order_by(InteractionEvent.id)
        ]


def test_a_page_turn_is_recorded(book_with_text):
    client, _ = book_with_text
    response = client.get("/api/books/1/pages", params={"page": 2, "page_size": 1})

    assert response.status_code == 200
    assert response.json()["total"] == 3, "fixture should produce a three-page excerpt"
    recorded = _page_events()
    assert len(recorded) == 1
    assert recorded[0] == {
        "page": 2, "page_size": 1, "excerpt_pages": 3, "had_content": True,
    }


def test_paging_past_the_excerpt_is_not_counted_as_reading(book_with_text):
    """The pager offers page 300 of an 11-page excerpt. Clicking through the
    blank pages must not read as a reader who went further."""
    client, _ = book_with_text
    client.get("/api/books/1/pages", params={"page": 40, "page_size": 1})

    recorded = _page_events()
    assert recorded[0]["had_content"] is False
    assert recorded[0]["excerpt_pages"] == 3


def test_a_book_with_no_text_is_logged_but_not_as_reading(fitted_app):
    """Recorded as a content gap, excluded from depth."""
    from db import SessionLocal
    from models import BookText, InteractionEvent
    import store

    main, client = fitted_app
    for book_id, book in main.BOOK_BY_ID.items():
        with SessionLocal() as session:
            pk = store.resolve_book_pk(session, book)
            if pk is not None and session.get(BookText, pk) is None:
                session.query(InteractionEvent).delete()
                session.commit()
                break
    else:
        pytest.skip("every book has text in this database")

    client.get(f"/api/books/{book_id}/pages", params={"page": 1, "page_size": 1})
    recorded = _page_events()
    assert recorded and recorded[-1]["had_content"] is False
    assert recorded[-1]["excerpt_pages"] == 0


def test_bulk_fetches_are_distinguishable_from_page_turns(book_with_text):
    """The reader asks for one page; anything else is a bulk fetch. Keeping
    page_size lets aggregation count only genuine turns."""
    client, _ = book_with_text
    client.get("/api/books/1/pages")  # default page_size

    assert _page_events()[0]["page_size"] == 24


def test_a_logging_failure_does_not_stop_anyone_reading(book_with_text, monkeypatch):
    """F-42's first rule: an analytics outage must not become a product outage."""
    client, _ = book_with_text

    def explode(*a, **k):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(events, "SessionLocal", explode)
    response = client.get("/api/books/1/pages", params={"page": 1, "page_size": 1})

    assert response.status_code == 200
    assert response.json()["items"], "reading broke because logging failed"
