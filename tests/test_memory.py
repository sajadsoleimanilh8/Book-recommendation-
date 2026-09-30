"""AI Book Memory — section 33, Phase 5.

Section 33's own constraint is the thing to test: "do not replay raw
transcripts forever." That is a claim about storage, not just prompt
construction, so most of this file is about what actually lands in
`book_memory` — never a raw question or a full answer — rather than about
summarisation quality, which no test can meaningfully grade.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

from services.memory import (  # noqa: E402
    MAX_SUMMARY_CHARS,
    format_prior_context,
    summarize_turn,
)


class _Script:
    def __init__(self, content):
        self.content = content

    def chat(self, messages, *, tools=None):
        class _R:
            pass

        r = _R()
        r.content = self.content
        return r


class _Boom:
    def chat(self, messages, *, tools=None):
        raise RuntimeError("model exploded")


# --------------------------------------------------------------------------
# summarize_turn — the property that matters, no database needed
# --------------------------------------------------------------------------


def test_the_raw_question_and_answer_never_appear_verbatim_when_a_model_is_used():
    """The property section 33 exists for. A summary is allowed to be
    short and lossy; it is not allowed to just be the transcript again."""
    question = "What does the narrator think about her sister's decision to leave?"
    answer = (
        "The narrator is conflicted: she resents her sister for leaving but "
        "also envies her courage, and the passage suggests she has not "
        "forgiven her, though she claims otherwise."
    )
    llm = _Script("Asked about the narrator's feelings toward her sister leaving.")

    summary = summarize_turn(llm, question, answer)

    assert summary != question and summary != answer
    assert answer not in summary, "the full answer was stored, not a summary of it"


def test_a_long_summary_is_truncated_not_stored_in_full():
    """A model that ignores the length instruction must not turn this back
    into transcript storage by another name."""
    llm = _Script("word " * 200)
    summary = summarize_turn(llm, "q", "a")
    assert len(summary) <= MAX_SUMMARY_CHARS


def test_truncation_cuts_at_a_word_boundary():
    llm = _Script("word " * 200)
    summary = summarize_turn(llm, "q", "a")
    assert not summary.endswith("wor…"), "cut mid-word instead of at a boundary"


def test_no_model_still_produces_a_short_fallback_summary():
    """Section 12's shape again: memory degrades, it does not stop being
    recorded, during a model outage."""
    summary = summarize_turn(None, "What happens in chapter four?", "irrelevant")
    assert summary and len(summary) <= MAX_SUMMARY_CHARS


def test_a_model_that_raises_falls_back_rather_than_propagating():
    """A summarisation failure must not take down the turn that already
    produced a real, grounded answer -- the answer is the valuable part."""
    summary = summarize_turn(_Boom(), "What happens?", "It rains.")
    assert summary  # did not raise, produced something


def test_an_empty_model_reply_falls_back_too():
    llm = _Script("   ")
    summary = summarize_turn(llm, "What happens?", "It rains.")
    assert summary.strip()


# --------------------------------------------------------------------------
# format_prior_context — pure formatting, no database needed
# --------------------------------------------------------------------------


def test_no_summaries_is_no_context_not_empty_scaffolding():
    """A first-ever conversation about a book must not carry a hollow
    'Earlier in this conversation:' header into the prompt."""
    assert format_prior_context([]) is None


def test_summaries_are_presented_oldest_first():
    """Stored and fetched most-recent-first; a reader's own history should
    still read as a timeline, not backwards."""
    # Most-recent-first input, as recent_memory would supply it.
    context = format_prior_context(["asked about chapter 5", "asked about chapter 2"])
    assert context.index("chapter 2") < context.index("chapter 5")


# --------------------------------------------------------------------------
# record_turn / recent_memory — the real storage and retrieval path
# --------------------------------------------------------------------------


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
class TestStorage:
    @pytest.fixture(scope="class")
    def book_ids(self):
        """Real ids from the catalogue. `book_memory.book_id` has a genuine
        FK to `books`, and this database's ids do not start at 1 — they
        run from wherever the sequence landed after past restores (F-59
        /F-61) — so a hardcoded small integer is not a book here."""
        from db import SessionLocal
        from models import Book
        from sqlalchemy import select

        with SessionLocal() as session:
            return session.scalars(select(Book.id).order_by(Book.id).limit(6)).all()

    @pytest.fixture
    def reader(self):
        import uuid

        from db import SessionLocal
        from models import User

        tag = uuid.uuid4().hex[:12]
        with SessionLocal() as session:
            user = User(email=f"memory-{tag}@example.com", password_hash="x")
            session.add(user)
            session.commit()
            user_id = user.id

        yield user_id

        with SessionLocal() as session:
            u = session.get(User, user_id)
            if u is not None:
                session.delete(u)
                session.commit()

    def test_record_then_recall_round_trips(self, reader, book_ids):
        from db import SessionLocal
        from services.memory import record_turn, recent_memory

        with SessionLocal() as session:
            record_turn(
                session, user_id=reader, book_id=book_ids[0],
                question="What happens in chapter one?",
                answer="It opens on a stormy night.",
                llm=_Script("Asked about the opening chapter."),
            )
            recalled = recent_memory(session, user_id=reader, book_id=book_ids[0])

        assert recalled == ["Asked about the opening chapter."]

    def test_an_anonymous_caller_records_nothing(self, reader, book_ids):
        """Memory is per reader. There is no reader to attach an
        anonymous turn to, so recording it silently is not an option --
        it would either be orphaned or, worse, attributed to nobody
        correctly and everybody by accident."""
        from db import SessionLocal
        from services.memory import record_turn

        with SessionLocal() as session:
            result = record_turn(
                session, user_id=None, book_id=book_ids[0],
                question="q", answer="a", llm=_Script("s"),
            )

        assert result is None

    def test_recent_memory_for_an_anonymous_caller_is_empty_not_an_error(self, book_ids):
        from db import SessionLocal
        from services.memory import recent_memory

        with SessionLocal() as session:
            assert recent_memory(session, user_id=None, book_id=book_ids[0]) == []

    def test_memory_is_scoped_to_the_book_it_was_recorded_against(self, reader, book_ids):
        """The other reader never sees this: a question about one book must
        not surface as prior context for a question about a different one."""
        from db import SessionLocal
        from services.memory import record_turn, recent_memory

        with SessionLocal() as session:
            record_turn(
                session, user_id=reader, book_id=book_ids[0],
                question="q1", answer="a1", llm=_Script("about book one"),
            )
            record_turn(
                session, user_id=reader, book_id=book_ids[1],
                question="q2", answer="a2", llm=_Script("about book two"),
            )
            first_book_memory = recent_memory(session, user_id=reader, book_id=book_ids[0])

        assert first_book_memory == ["about book one"]

    def test_memory_is_scoped_to_the_reader_who_made_it(self, reader, book_ids):
        """The property that matters most, symmetric with search_chunks'
        own visibility guarantee: one reader's memory of a book must not
        surface for a different reader asking about the same book."""
        import uuid

        from db import SessionLocal
        from models import User
        from services.memory import record_turn, recent_memory

        tag = uuid.uuid4().hex[:12]
        with SessionLocal() as session:
            other = User(email=f"memory-other-{tag}@example.com", password_hash="x")
            session.add(other)
            session.commit()
            other_id = other.id

        try:
            with SessionLocal() as session:
                record_turn(
                    session, user_id=reader, book_id=book_ids[2],
                    question="q", answer="a", llm=_Script("reader's own note"),
                )
                other_memory = recent_memory(session, user_id=other_id, book_id=book_ids[2])
            assert other_memory == [], "another reader's memory leaked across accounts"
        finally:
            with SessionLocal() as session:
                u = session.get(User, other_id)
                if u is not None:
                    session.delete(u)
                    session.commit()

    def test_recent_memory_is_bounded_and_most_recent_first(self, reader, book_ids):
        from db import SessionLocal
        from services.memory import RECENT_MEMORY_LIMIT, record_turn, recent_memory

        with SessionLocal() as session:
            for i in range(RECENT_MEMORY_LIMIT + 3):
                record_turn(
                    session, user_id=reader, book_id=book_ids[3],
                    question=f"q{i}", answer=f"a{i}", llm=_Script(f"note {i}"),
                )
            recalled = recent_memory(session, user_id=reader, book_id=book_ids[3])

        assert len(recalled) == RECENT_MEMORY_LIMIT
        assert recalled[0] == f"note {RECENT_MEMORY_LIMIT + 2}", (
            "most recent turn is not first"
        )

    def test_two_turns_produce_two_rows_not_one_overwritten(self, reader, book_ids):
        """Append-only. A reader's history of what they discussed is a
        real record, not one blob a second write could quietly erase."""
        from db import SessionLocal
        from models import BookMemory
        from sqlalchemy import select
        from services.memory import record_turn

        with SessionLocal() as session:
            record_turn(
                session, user_id=reader, book_id=book_ids[4],
                question="q1", answer="a1", llm=_Script("first note"),
            )
            record_turn(
                session, user_id=reader, book_id=book_ids[4],
                question="q2", answer="a2", llm=_Script("second note"),
            )
            rows = session.scalars(
                select(BookMemory).where(
                    BookMemory.user_id == reader, BookMemory.book_id == book_ids[4]
                )
            ).all()

        assert len(rows) == 2
        assert {r.summary for r in rows} == {"first note", "second note"}
