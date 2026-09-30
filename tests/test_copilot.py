"""Reading Copilot — section 32, Phase 5.

The property under test is the one section 32 makes load-bearing: the
assistant must distinguish `Known from book` / `Inference` / `Uncertain`.
A version that asks the model for that label and prints it would satisfy
the letter of the spec while making the most consequential field in the
response the one nothing checks — the same shape F-22's grounding guard
exists to prevent for book titles.

So these tests are mostly about what happens when the model lies, or is
sloppy, or is absent: a fake LLM throughout, plus the real retrieval path
against real chunks for the parts where the database is what is being
proved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from conftest import database_reachable  # noqa: E402

from services import copilot  # noqa: E402
from services.copilot import (  # noqa: E402
    INFERENCE,
    KNOWN,
    UNCERTAIN,
    ask_about_book,
    summarize_book,
    unsupported_quotes,
)


class _Script:
    """An LLM that returns one fixed reply and records what it was sent."""

    def __init__(self, content, model="script"):
        self.content = content
        self.model = model
        self.sent = []

    def chat(self, messages, *, tools=None):
        self.sent.append(messages)

        class _R:
            pass

        r = _R()
        r.content = self.content
        r.model = self.model
        r.tool_calls = []
        return r


# --------------------------------------------------------------------------
# unsupported_quotes — the check itself, no database needed
# --------------------------------------------------------------------------

PASSAGE = (
    "It was a dark and stormy night, and the wind howled through the old "
    "manor house, rattling every window in its frame."
)


def test_a_quote_that_is_in_a_passage_is_supported():
    answer = 'The opening sets the mood: "the wind howled through the old manor house".'
    assert unsupported_quotes(answer, [PASSAGE]) == []


def test_a_quote_that_is_in_no_passage_is_reported():
    answer = 'The narrator says "the sun blazed over a calm blue harbour" early on.'
    bad = unsupported_quotes(answer, [PASSAGE])
    assert len(bad) == 1 and "calm blue harbour" in bad[0]


def test_punctuation_and_spacing_differences_are_not_treated_as_invention():
    """The model reflowing whitespace or dropping a comma inside a quote is
    not the failure this guard is for — inventing text is."""
    answer = 'It opens: "It was a dark   and stormy night and the wind howled".'
    assert unsupported_quotes(answer, [PASSAGE]) == []


def test_a_two_word_quote_is_emphasis_not_a_claim_about_the_text():
    """Quoting a term of art must not be treated as quoting the book, or
    every answer that emphasises a word gets flagged."""
    answer = 'This is the book\'s "mood" throughout.'
    assert unsupported_quotes(answer, [PASSAGE]) == []


def test_smart_quotes_are_checked_too():
    answer = "The narrator says “the sun blazed over a calm blue harbour” here."
    assert len(unsupported_quotes(answer, [PASSAGE])) == 1


# --------------------------------------------------------------------------
# The label, and what happens when the model's claim is wrong
# --------------------------------------------------------------------------


class _FakeHit:
    def __init__(self, passage, chunk_id=1, ordinal=0, similarity=0.5, origin="text"):
        self.passage = passage
        self.chunk_id = chunk_id
        self.ordinal = ordinal
        self.similarity = similarity
        self.origin = origin
        self.book_id = 42
        self.title = "A Book"
        self.author = "An Author"
        self.visibility = "public"


@pytest.fixture
def retrieval(monkeypatch):
    """Replace retrieval so these tests are about the grounding logic, not
    about pgvector. The real retrieval path has its own tests."""
    def _set(hits):
        monkeypatch.setattr(copilot, "search_chunks", lambda *a, **k: list(hits))

    return _set


def test_a_claimed_known_label_survives_when_every_quote_checks_out(retrieval):
    retrieval([_FakeHit(PASSAGE)])
    llm = _Script('It opens with "the wind howled through the old manor house".\nGROUNDING: known_from_book')

    result = ask_about_book(
        None, book_id=42, question="How does it open?", query_vector=[0.1], llm=llm
    )

    assert result["grounding"] == KNOWN
    assert result["label_downgraded_from"] is None
    assert result["unsupported_quotes"] == []
    assert "GROUNDING:" not in result["answer"], "the label line leaked into the answer"


def test_a_known_label_is_downgraded_when_a_quote_is_invented(retrieval):
    """The test this module exists for. The model claims the book said
    something, the passages do not contain it, and the response must not
    carry `known_from_book` on the model's word alone.
    """
    retrieval([_FakeHit(PASSAGE)])
    llm = _Script('The narrator says "the sun blazed over a calm blue harbour".\nGROUNDING: known_from_book')

    result = ask_about_book(
        None, book_id=42, question="What is the weather?", query_vector=[0.1], llm=llm
    )

    assert result["grounding"] == UNCERTAIN
    assert result["label_downgraded_from"] == KNOWN
    assert result["unsupported_quotes"], "the offending quote is not reported"


def test_an_inference_label_is_passed_through(retrieval):
    retrieval([_FakeHit(PASSAGE)])
    llm = _Script("The mood is ominous, though the text does not say so outright.\nGROUNDING: inference")

    result = ask_about_book(
        None, book_id=42, question="What is the mood?", query_vector=[0.1], llm=llm
    )
    assert result["grounding"] == INFERENCE


def test_a_missing_label_defaults_to_uncertain_not_known(retrieval):
    """A model that ignores the format must not be read as confident."""
    retrieval([_FakeHit(PASSAGE)])
    llm = _Script("It opens on a stormy night.")

    result = ask_about_book(
        None, book_id=42, question="How does it open?", query_vector=[0.1], llm=llm
    )
    assert result["grounding"] == UNCERTAIN


def test_no_passages_means_uncertain_and_the_model_is_never_called(retrieval):
    """Check 1. There is nothing in the book to have known, and spending a
    model call to discover that is how a confident answer about an
    unavailable book gets produced."""
    retrieval([])
    llm = _Script("Certainly! The book says a great many things.\nGROUNDING: known_from_book")

    result = ask_about_book(
        None, book_id=42, question="Anything?", query_vector=[0.1], llm=llm
    )

    assert result["grounding"] == UNCERTAIN
    assert result["citations"] == []
    assert result["passages_considered"] == 0
    assert llm.sent == [], "the model was called with nothing to ground on"


def test_no_model_still_returns_the_passages_it_retrieved(retrieval):
    """Section 12's shape: an outage degrades this endpoint to retrieval,
    which is still useful, rather than failing it."""
    retrieval([_FakeHit(PASSAGE)])

    result = ask_about_book(
        None, book_id=42, question="How does it open?", query_vector=[0.1], llm=None
    )

    assert result["ok"] is True
    assert result["grounding"] == UNCERTAIN
    assert len(result["citations"]) == 1
    assert result["citations"][0]["passage"] == PASSAGE


def test_an_empty_question_is_refused(retrieval):
    retrieval([_FakeHit(PASSAGE)])
    result = ask_about_book(None, book_id=42, question="   ", query_vector=[0.1])
    assert result["ok"] is False


def test_the_passages_are_actually_given_to_the_model(retrieval):
    """A prompt that forgets to include the retrieved text would still
    produce plausible answers — from the model's own memory of the book,
    which is exactly what the whole pipeline exists to avoid."""
    retrieval([_FakeHit(PASSAGE)])
    llm = _Script("ok\nGROUNDING: inference")

    ask_about_book(None, book_id=42, question="q", query_vector=[0.1], llm=llm)

    sent = "\n".join(m["content"] for m in llm.sent[0])
    assert PASSAGE in sent, "the model was asked to answer without the passages"
    assert "q" in sent


def test_citations_are_bounded(retrieval):
    retrieval([_FakeHit(PASSAGE, chunk_id=i) for i in range(20)])
    llm = _Script("ok\nGROUNDING: inference")

    result = ask_about_book(None, book_id=42, question="q", query_vector=[0.1], llm=llm)

    assert len(result["citations"]) == copilot.MAX_CITATIONS
    assert result["passages_considered"] == 20, (
        "the count of what was considered should not be trimmed with the citations"
    )


# --------------------------------------------------------------------------
# The real retrieval path — book scoping and visibility, against real rows
# --------------------------------------------------------------------------


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_it_answers_only_from_the_book_it_was_asked_about():
    """The copilot's retrieval must be the scoped one. Asked about a book
    with no chunks, it must report having nothing rather than answering
    from whichever passage in the corpus is nearest."""
    from db import SessionLocal
    from embeddings import get_backend

    encoder = get_backend("hashing")
    vector = encoder.encode(["whatever this book says about anything"])[0].tolist()
    llm = _Script("The book explains it.\nGROUNDING: known_from_book")

    with SessionLocal() as session:
        result = ask_about_book(
            session, book_id=-1, question="What happens?",
            query_vector=vector, llm=llm, embedding_model="hashing",
        )

    assert result["passages_considered"] == 0
    assert result["grounding"] == UNCERTAIN
    assert llm.sent == []


# --------------------------------------------------------------------------
# The endpoint — auth, wiring and response shape, through the real app
# --------------------------------------------------------------------------


@pytest.fixture
def app_client(fitted_app, monkeypatch):
    """The real app, with the model replaced. Everything else on the path —
    encoder, retrieval, auth, the rate limiter — is real, because those are
    the parts the service-level tests above deliberately do not cover."""
    import ratelimit
    from services.providers import llm as llm_module

    main_mod, client = fitted_app
    ratelimit.reset()

    scripted = _Script("It opens on a stormy night.\nGROUNDING: inference")
    monkeypatch.setattr(llm_module, "get_llm_provider", lambda: scripted)
    monkeypatch.setattr("api.copilot.get_llm_provider", lambda: scripted)
    yield main_mod, client, scripted
    ratelimit.reset()


def _first_book_with_chunks():
    from db import SessionLocal
    from models import BookChunk
    from sqlalchemy import select

    with SessionLocal() as session:
        return session.scalar(
            select(BookChunk.book_id).where(BookChunk.visibility == "public").limit(1)
        )


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_endpoint_answers_with_citations_and_a_label(app_client):
    _, client, _ = app_client
    book_id = _first_book_with_chunks()
    assert book_id is not None, "no public chunks in the test database"

    r = client.post(f"/api/books/{book_id}/ask", json={"question": "How does it open?"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["book_id"] == book_id
    assert body["grounding"] in {KNOWN, INFERENCE, UNCERTAIN}
    assert body["citations"], "answered without citing anything"
    assert all("passage" in c and "ordinal" in c for c in body["citations"])


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_endpoint_is_reachable_anonymously(app_client):
    """`OptionalUser`: a public-domain book is answerable without an account,
    and `search_chunks` is what decides that, not this route."""
    _, client, _ = app_client
    book_id = _first_book_with_chunks()

    r = client.post(f"/api/books/{book_id}/ask", json={"question": "What happens?"})
    assert r.status_code == 200


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_endpoint_rejects_an_identity_in_the_body(app_client):
    """`extra="forbid"`. The one field that must never be accepted here is a
    caller-supplied id — the same rule every librarian tool follows."""
    _, client, _ = app_client
    book_id = _first_book_with_chunks()

    r = client.post(
        f"/api/books/{book_id}/ask",
        json={"question": "What happens?", "user_id": 99},
    )
    assert r.status_code == 422


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_endpoint_rejects_an_empty_question(app_client):
    _, client, _ = app_client
    book_id = _first_book_with_chunks()

    r = client.post(f"/api/books/{book_id}/ask", json={"question": ""})
    assert r.status_code == 422


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_a_book_with_no_text_is_answered_honestly_not_from_another_book(app_client):
    """The endpoint-level version of the property that matters: a book the
    system holds no text for gets an honest 'I cannot say', never an answer
    assembled from a different book's passages."""
    _, client, scripted = app_client

    r = client.post("/api/books/999999999/ask", json={"question": "What happens?"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["grounding"] == UNCERTAIN
    assert body["citations"] == []
    assert body["passages_considered"] == 0
    assert scripted.sent == [], "the model was consulted about a book with no text"


# --------------------------------------------------------------------------
# Section 33's memory, wired through the real endpoint
# --------------------------------------------------------------------------


class _RoutingScript:
    """Distinguishes the main answer prompt from `services.memory`'s
    summarisation prompt, so a test can assert on each separately instead
    of both collapsing to one fixed reply."""

    def __init__(self, answer_content, summary_content):
        self.answer_content = answer_content
        self.summary_content = summary_content
        self.sent = []

    def chat(self, messages, *, tools=None):
        self.sent.append(messages)
        text = messages[-1]["content"]
        content = self.summary_content if "Summarise this one exchange" in text else self.answer_content

        class _R:
            pass

        r = _R()
        r.content = content
        r.model = "routing-script"
        r.tool_calls = []
        return r


@pytest.fixture
def signed_in_client(fitted_app, monkeypatch):
    """A registered reader, not an anonymous one — memory is per reader and
    no-ops entirely for `user_id=None`, so exercising it needs an account."""
    import uuid

    import ratelimit

    main_mod, client = fitted_app
    ratelimit.reset()

    tag = uuid.uuid4().hex[:12]
    r = client.post(
        "/api/auth/register",
        json={"email": f"copilot-{tag}@example.com", "password": "correct horse battery"},
    )
    assert r.status_code < 400, r.text
    token = r.json()["access_token"]
    user_id = r.json()["user"]["id"]

    yield client, {"Authorization": f"Bearer {token}"}, user_id

    ratelimit.reset()
    from db import SessionLocal
    from models import User

    with SessionLocal() as session:
        u = session.get(User, user_id)
        if u is not None:
            session.delete(u)  # book_memory cascades
            session.commit()


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_a_second_question_carries_the_first_ones_memory(signed_in_client, monkeypatch):
    """The end-to-end proof of section 33: ask about a book, ask again, and
    the second call's own prompt to the model must contain a trace of the
    first — not the raw question or full answer (that would be exactly the
    transcript replay section 33 forbids), a short derived summary.
    """
    client, headers, _ = signed_in_client
    book_id = _first_book_with_chunks()
    assert book_id is not None

    scripted = _RoutingScript(
        answer_content="It opens on a dark and stormy night.\nGROUNDING: inference",
        summary_content="Asked about how the book opens; told it starts on a stormy night.",
    )
    monkeypatch.setattr("api.copilot.get_llm_provider", lambda: scripted)

    first = client.post(
        f"/api/books/{book_id}/ask", json={"question": "How does it open?"}, headers=headers
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/books/{book_id}/ask", json={"question": "Tell me more."}, headers=headers
    )
    assert second.status_code == 200, second.text

    # The message actually sent to the model for the second question.
    second_answer_call = next(
        m for m in scripted.sent
        if "Summarise this one exchange" not in m[-1]["content"]
        and m is not scripted.sent[0]
    )
    sent_text = second_answer_call[-1]["content"]

    assert "Asked about how the book opens" in sent_text, (
        "the first turn's memory did not reach the second turn's prompt"
    )
    assert "It opens on a dark and stormy night" not in sent_text, (
        "the full first answer leaked into the prompt instead of a summary of it"
    )


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_an_anonymous_caller_leaves_no_memory_behind(app_client):
    """No account, no row -- `services.memory` already guarantees this;
    this is the endpoint-level confirmation that the wiring respects it."""
    from db import SessionLocal
    from models import BookMemory
    from sqlalchemy import select

    _, client, _ = app_client
    book_id = _first_book_with_chunks()

    client.post(f"/api/books/{book_id}/ask", json={"question": "What happens?"})

    with SessionLocal() as session:
        count = session.scalar(
            select(BookMemory).where(BookMemory.book_id == book_id)
        )
    assert count is None, "an anonymous question was recorded as a reader's memory"


# --------------------------------------------------------------------------
# summarize_book — section 38's "summaries", same grounding discipline
# --------------------------------------------------------------------------


@pytest.fixture
def representative(monkeypatch):
    """Replace `representative_chunks` the same way `retrieval` replaces
    `search_chunks` above: these tests are about the summarisation and
    quote-checking logic, not about picking rows out of the database."""
    def _set(hits):
        monkeypatch.setattr(copilot, "representative_chunks", lambda *a, **k: list(hits))

    return _set


def test_no_passages_means_no_model_call_for_a_summary(representative):
    representative([])
    llm = _Script("This book is about many things.")

    result = summarize_book(None, book_id=42, llm=llm)

    assert result["ok"] is True
    assert result["citations"] == []
    assert result["passages_considered"] == 0
    assert llm.sent == [], "the model was called with nothing to summarise"


def test_no_model_still_returns_the_sampled_passages(representative):
    representative([_FakeHit(PASSAGE)])

    result = summarize_book(None, book_id=42, llm=None)

    assert result["ok"] is True
    assert len(result["citations"]) == 1
    assert result["citations"][0]["passage"] == PASSAGE
    assert result["model"] is None


def test_the_sampled_passages_are_actually_given_to_the_model(representative):
    representative([_FakeHit(PASSAGE)])
    llm = _Script("A short summary of the sampled passages.")

    summarize_book(None, book_id=42, llm=llm)

    sent = "\n".join(m["content"] for m in llm.sent[0])
    assert PASSAGE in sent, "the model was asked to summarise without the passages"


def test_summary_citations_are_bounded(representative):
    representative([_FakeHit(PASSAGE, chunk_id=i) for i in range(20)])
    llm = _Script("A short summary.")

    result = summarize_book(None, book_id=42, llm=llm)

    assert len(result["citations"]) == copilot.SUMMARY_MAX_CITATIONS
    assert result["passages_considered"] == 20, (
        "the count of what was sampled should not be trimmed with the citations"
    )


def test_an_unsupported_quote_in_a_summary_is_reported(representative):
    """The same guard `ask_about_book` applies to a claimed label applies
    here to the summary text itself: a quoted span not in any sampled
    passage is invented, and must be reported, not silently accepted."""
    representative([_FakeHit(PASSAGE)])
    llm = _Script('This book opens with "a sun blazed over a calm blue harbour".')

    result = summarize_book(None, book_id=42, llm=llm)

    assert len(result["unsupported_quotes"]) == 1


def test_a_summary_with_only_supported_quotes_reports_none(representative):
    representative([_FakeHit(PASSAGE)])
    llm = _Script('It opens: "the wind howled through the old manor house".')

    result = summarize_book(None, book_id=42, llm=llm)

    assert result["unsupported_quotes"] == []


# --------------------------------------------------------------------------
# GET /api/books/{book_id}/summary — through the real app
# --------------------------------------------------------------------------


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_summary_endpoint_returns_a_summary_with_citations(app_client):
    _, client, _ = app_client
    book_id = _first_book_with_chunks()
    assert book_id is not None, "no public chunks in the test database"

    r = client.get(f"/api/books/{book_id}/summary")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["book_id"] == book_id
    assert body["citations"], "summarised without citing anything sampled"
    assert "unsupported_quotes" in body


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_the_summary_endpoint_is_reachable_anonymously(app_client):
    _, client, _ = app_client
    book_id = _first_book_with_chunks()

    r = client.get(f"/api/books/{book_id}/summary")
    assert r.status_code == 200


@pytest.mark.skipif(not database_reachable(), reason="Postgres not reachable")
def test_a_book_with_no_text_is_summarized_honestly_not_from_another_book(app_client):
    _, client, scripted = app_client

    r = client.get("/api/books/999999999/summary")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["citations"] == []
    assert body["passages_considered"] == 0
    assert scripted.sent == [], "the model was consulted about a book with no text"
