"""F-44 — one embedding per book, built the same way for every book.

The bug this exists to prevent was caught in design, not production. Swapping
`content_s` to the MiniLM vectors in `book_chunks` looks like a pure upgrade —
84.0% same-book@10 against LSA's 50.7% — but chunks only exist for the 23.3%
of books that have a description or Gutenberg text. Content similarity carries
weight 0.28 directly and feeds the LTR component's 0.32, so three quarters of
the catalogue would have silently stopped being recommendable on content.
Nothing would have errored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import book_vector_pass  # noqa: E402
from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres not reachable"
)


# --- the recipe (no database needed) --------------------------------------


class _Book:
    def __init__(self, title="", author="", genre="", description=None):
        self.title, self.author = title, author
        self.genre, self.description = genre, description


def test_a_book_with_no_description_still_produces_text():
    """The whole point of F-44. 76.7% of the catalogue is in this case."""
    text, had_description, chars = book_vector_pass.build_text(
        _Book(title="Dune", author="Frank Herbert", genre="Science Fiction")
    )
    assert text == "Dune. Frank Herbert. Science Fiction"
    assert had_description is False
    assert chars > 0


def test_a_book_with_a_description_uses_the_same_recipe_plus_it():
    """Uniform construction. Two recipes would mean two populations inside one
    vector space, which is F-39 wearing a different hat."""
    text, had_description, _ = book_vector_pass.build_text(
        _Book(title="Dune", author="Frank Herbert", genre="Sci-Fi", description="Spice.")
    )
    assert text.startswith("Dune. Frank Herbert. Sci-Fi")
    assert text.endswith("Spice.")
    assert had_description is True


def test_a_very_long_description_is_truncated():
    """MiniLM attends to 512 tokens; the rest only slows the batch down."""
    text, _, _ = book_vector_pass.build_text(
        _Book(title="T", author="A", genre="G", description="x" * 50_000)
    )
    assert len(text) <= book_vector_pass.MAX_DESCRIPTION_CHARS + 100


def test_a_book_with_nothing_to_say_produces_no_text():
    """A zero vector would make every empty book similar to every other."""
    text, _, _ = book_vector_pass.build_text(_Book())
    assert text == ""


# --- against the database -------------------------------------------------
#
# These run the pass for real on a bounded sample. Asserting coverage over the
# whole production catalogue would test the environment rather than the code —
# it passes or fails depending on whether someone remembered to run a script.


SAMPLE = 300


@pytest.fixture(scope="module")
def built():
    """Run the pass over a sample and return what it produced."""
    from sqlalchemy import func, select

    from db import SessionLocal
    from models import Book, BookVector

    with SessionLocal() as session:
        if not session.scalar(select(func.count()).select_from(Book)):
            pytest.skip("catalogue not loaded in this database")

    result = book_vector_pass.run(limit=SAMPLE, redo=True)

    with SessionLocal() as session:
        ids = list(session.scalars(select(Book.id).order_by(Book.id).limit(SAMPLE)))
        rows = list(
            session.scalars(select(BookVector).where(BookVector.book_id.in_(ids)))
        )
    return result, ids, rows


def test_every_book_the_pass_saw_got_a_vector(built):
    """The F-44 property. A book with no vector cannot be recommended on
    content similarity, and nothing would report that."""
    result, ids, rows = built
    with_vector = {r.book_id for r in rows if r.embedding is not None}
    missing = set(ids) - with_vector
    assert not missing, (
        f"{len(missing)} of {len(ids)} books processed have no vector — that "
        f"slice drops out of content ranking silently. Pass reported {result}"
    )


def test_books_without_a_description_are_included(built):
    """The case that motivated all of this: 76.7% of the catalogue."""
    _, _, rows = built
    thin = [r for r in rows if not r.has_description]
    assert thin, "no metadata-only vectors — the F-44 path never fired"
    assert all(r.embedding is not None for r in thin)


def test_the_vectors_are_all_from_one_model(built):
    """Same guard as F-39: two models in one column is silently wrong."""
    _, _, rows = built
    models = {r.embedding_model for r in rows}
    assert len(models) == 1, f"more than one vector space present: {models}"


def test_a_vector_from_another_backend_is_rebuilt(built):
    """A backend switch must be self-healing, exactly as embed_pass is.

    Behavioural rather than source-inspecting: stamp a real row with a model
    name nothing uses, run an ordinary (non-redo) pass, require it was redone.
    """
    from db import SessionLocal
    from models import BookVector

    _, ids, _ = built
    target = ids[0]

    with SessionLocal() as session:
        session.get(BookVector, target).embedding_model = "a-backend-nothing-uses"
        session.commit()

    book_vector_pass.run(limit=SAMPLE)

    with SessionLocal() as session:
        refreshed = session.get(BookVector, target)
    assert refreshed.embedding_model != "a-backend-nothing-uses", (
        "a vector from another backend was left in place — the column now "
        "holds two vector spaces and nothing says so"
    )


def test_an_ordinary_rerun_does_not_redo_current_vectors(built):
    """The staleness rule, asserted on the rows it applies to.

    An earlier version of this test asserted `written == 0` and failed at
    `300 == 0`. That was the test being wrong, not the pass: skipping the
    current rows frees the limit to process the *next* 300 books, which is
    exactly what it should do against a 29,975-book catalogue. What actually
    matters is that the already-current rows were left alone.
    """
    from db import SessionLocal
    from models import BookVector

    _, ids, _ = built
    with SessionLocal() as session:
        before = {
            r.book_id: r.built_at
            for r in session.scalars(
                __import__("sqlalchemy").select(BookVector).where(
                    BookVector.book_id.in_(ids)
                )
            )
        }

    book_vector_pass.run(limit=SAMPLE)

    with SessionLocal() as session:
        after = {
            r.book_id: r.built_at
            for r in session.scalars(
                __import__("sqlalchemy").select(BookVector).where(
                    BookVector.book_id.in_(ids)
                )
            )
        }

    rebuilt = [bid for bid, ts in after.items() if before.get(bid) != ts]
    assert not rebuilt, (
        f"{len(rebuilt)} already-current vector(s) were rebuilt — the "
        "staleness filter is not doing its job"
    )
