"""Private uploads must not be visible to anything that walks the catalogue.

Phase 4, section 29. Every pass over `books` — enrichment, vector building,
Gutenberg text, ingest — was written when every row was catalogue, so each
one's "all books" meant what it said. These tests exist because the moment a
reader uploads a private book, "all books" silently stops meaning that.

Two of the four were not merely untidy:

  * `enrich.pending_query` takes `sources: list[str] | None = None` and
    applies no source filter when that is None, so an uploaded book would
    have been queued and its title and author sent to Google Books — a third
    party learning what is in a reader's private library, paid for out of
    the quota that is this project's binding constraint.
  * `ingest` **deletes** every book whose (source, external_id) is absent
    from the catalogue file. An upload is never in that file, so every
    upload on the instance would have been destroyed by the next
    `python -m ingest` — which the README tells people to re-run freely
    because it is idempotent.

The isolation is asserted against real rows rather than by reading the
generated SQL, because the question is what comes back, not what the
statement looks like.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

pytest.importorskip("sqlalchemy", reason="persistence stack not installed")

from conftest import database_reachable  # noqa: E402

pytestmark = pytest.mark.skipif(
    not database_reachable(), reason="Postgres unreachable"
)


@pytest.fixture
def upload():
    """One reader and one private book of theirs, removed afterwards.

    Cleaned up explicitly rather than left for the suite to trip over: a
    fixture that leaves rows behind is how Phase A cost ten golden baselines.
    The user is deleted last and `books.owner_id` cascades, so a failure
    part-way through still leaves nothing.
    """
    from db import SessionLocal
    from models import Book, User

    tag = uuid.uuid4().hex[:12]
    with SessionLocal() as session:
        user = User(
            email=f"upload-{tag}@example.com",
            password_hash="x",
            display_name="Upload Tester",
        )
        session.add(user)
        session.flush()

        book = Book(
            source="upload",
            external_id=tag,
            owner_id=user.id,
            title=f"A Private Book {tag}",
            author="Its Owner",
            enrichment_status="pending",
        )
        session.add(book)
        session.commit()
        ids = (user.id, book.id)

    yield ids

    with SessionLocal() as session:
        user = session.get(User, ids[0])
        if user is not None:
            session.delete(user)  # books.owner_id cascades
            session.commit()


def _ids(stmt) -> set[int]:
    from db import SessionLocal
    from models import Book

    with SessionLocal() as session:
        rows = session.execute(stmt).all()
    out = set()
    for row in rows:
        value = row[0]
        out.add(value.id if isinstance(value, Book) else value)
    return out


# -- the four passes -------------------------------------------------------


def test_enrichment_never_queues_a_private_upload(upload):
    """The one that would have sent a reader's book title to Google."""
    from scripts.enrich import pending_query

    _, book_id = upload
    queued = _ids(pending_query(limit=100_000, languages=None, sources=None))

    assert book_id not in queued, (
        "an uploaded book was queued for third-party enrichment"
    )


def test_enrichment_isolation_holds_with_no_source_filter(upload):
    """`sources=None` means 'every source'. That default is what made this a
    hole rather than a hypothetical, so it is the case pinned here."""
    from scripts.enrich import pending_query

    _, book_id = upload
    for sources in (None, [], ["upload"], ["google_books", "upload"]):
        queued = _ids(pending_query(limit=100_000, languages=None, sources=sources))
        assert book_id not in queued, f"leaked with sources={sources!r}"


def test_vector_building_never_embeds_a_private_upload(upload):
    """`book_vectors` has no `visibility` column and no owner, and
    `services.search.search_books` queries it directly — so a vector built
    for an upload is a public search result for every reader on the
    platform. This is why private retrieval rides `book_chunks` instead."""
    from scripts.book_vector_pass import pending_query

    _, book_id = upload
    queued = _ids(pending_query(limit=100_000, redo=False, model_name="minilm"))

    assert book_id not in queued, "an upload was queued for a public search vector"


def test_the_gutenberg_pass_never_touches_a_private_upload(upload):
    from scripts.gutenberg_pass import pending_query

    _, book_id = upload
    assert book_id not in _ids(pending_query(limit=100_000))


def test_reingesting_the_catalogue_does_not_delete_uploads(upload):
    """The data-loss one.

    `ingest.prune` removes every book absent from the catalogue file, and an
    upload is never in that file.

    Asserted in two halves rather than by calling `prune()`, because calling
    it is itself destructive: the first version of this test did, and it
    deleted all 29,975 books from `digikitab_test`, cascading to the vectors
    the golden ranking baselines depend on. The suite went from green to
    eleven failures, and the cause was the test, not the code — Phase A's
    fixture bug, repeated exactly.

    So: check that the row survives the set `prune` iterates, and separately
    that `prune` iterates that set. Together those are the behavioural claim,
    without a function that commits deletions to whatever database happens to
    be configured.
    """
    import re

    from sqlalchemy import select

    from models import Book, catalogue_only

    _, book_id = upload

    # Half one: the upload is not among the rows prune walks.
    assert book_id not in _ids(catalogue_only(select(Book.id)))

    # Half two: prune walks exactly that. A test of the query alone would
    # keep passing if someone dropped the filter from the loop.
    src = (BACKEND / "ingest.py").read_text(encoding="utf-8")
    body = src.split("def prune(", 1)[1].split("\ndef ", 1)[0]
    loop = next(
        line for line in body.splitlines() if "session.scalars(" in line
    )
    assert re.search(r"catalogue_only\s*\(", loop), (
        f"prune's delete loop no longer filters to the catalogue: {loop.strip()}"
    )


# -- the helper itself -----------------------------------------------------


def test_catalogue_only_excludes_uploads_and_keeps_catalogue(upload):
    from sqlalchemy import select

    from models import Book, catalogue_only

    _, book_id = upload
    everything = _ids(select(Book.id))
    catalogue = _ids(catalogue_only(select(Book.id)))

    assert book_id in everything, "the fixture did not create an upload"
    assert book_id not in catalogue
    assert len(catalogue) == len(everything) - 1, (
        "catalogue_only removed more than the upload"
    )


def test_owned_by_returns_only_that_readers_uploads(upload):
    """The other half. A reader listing their library must not see another
    reader's — F-07's shape, applied to Phase 4."""
    from sqlalchemy import select

    from models import Book, owned_by

    user_id, book_id = upload

    assert _ids(owned_by(select(Book.id), user_id)) == {book_id}
    assert _ids(owned_by(select(Book.id), user_id + 10_000)) == set()


def test_ownership_is_structural_not_a_source_string(upload):
    """`owner_id` is the marker, not `source == 'upload'`. A denylist on the
    source string fails open — the next private source anyone adds is
    included by default, invisibly."""
    from sqlalchemy import select

    from models import Book, catalogue_only

    _, book_id = upload
    from db import SessionLocal

    with SessionLocal() as session:
        book = session.get(Book, book_id)
        book.source = "google_books"  # lie about the source
        session.commit()

    assert book_id not in _ids(catalogue_only(select(Book.id))), (
        "isolation depends on the source string; an upload mislabelled as a "
        "catalogue source would rejoin the catalogue"
    )


# -- the suite's own cleanup must not eat the catalogue --------------------


def test_clearing_user_state_does_not_delete_the_catalogue():
    """F-61. `conftest.clean_user_state` promises "books are left alone".

    Phase 4 added `books.owner_id -> users.id`, and `TRUNCATE ... CASCADE`
    cascades by *table*, not by row — so adding that one foreign key silently
    enrolled `books`, `book_vectors`, `book_chunks` and `book_texts` in a
    statement meant to clear a handful of test rows. The catalogue was
    restored twice before the cause was found, because each restore vanished
    on the next run.

    This is asserted against the statement rather than by running it, because
    running it is the destructive act in question.
    """
    import ast
    import re

    # The function's code without its docstring. A plain text search found
    # the "TRUNCATE ... CASCADE" in the docstring *explaining the bug* and
    # failed on it — prose read as code, the same mistake the entrypoint
    # tests in test_deployment_gates.py had to be rewritten to avoid.
    tree = ast.parse((ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "clean_user_state"
    )
    statements = fn.body[1:] if ast.get_docstring(fn) else fn.body
    code = "\n".join(ast.unparse(node) for node in statements)

    truncated = re.search(r"TRUNCATE\s+([^\"']+)", code)
    assert truncated, "the cleanup no longer truncates anything — check this test"
    assert "users" not in truncated.group(1), (
        "`users` is back in the TRUNCATE list. Any table with a foreign key to "
        "it is emptied too, whether or not a row references it — which now "
        "includes `books`, and through it every vector, chunk and text."
    )
    assert "DELETE FROM users" in code, (
        "users must be cleared row-wise so that `owner_id IS NULL` survives"
    )


def test_a_deleted_reader_takes_their_uploads_and_nothing_else(upload):
    """The behaviour the row-level cascade is there to provide."""
    from sqlalchemy import select

    from db import SessionLocal
    from models import Book, User

    user_id, book_id = upload
    before = len(_ids(select(Book.id)))

    with SessionLocal() as session:
        session.delete(session.get(User, user_id))
        session.commit()

    after = _ids(select(Book.id))
    assert book_id not in after, "the reader's upload outlived them"
    assert len(after) == before - 1, "deleting one reader removed other books"
