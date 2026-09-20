"""The AI Librarian's tool loop — F-22 Phase C, prmpt.md section 28.

The property under test is not "the chatbot answers" but "the chatbot cannot
put a fact on the reader's screen that no tool returned". Several of these
tests exist because a *real* run of the loop violated that property:

  * the first real run answered "a price tag of $35.00 and is currently
    available" for a book whose price is unknown, after a search and nothing
    else (`test_an_invented_price_is_rejected`);
  * a hard genre filter, added because the model offered a `genre` argument,
    returned zero books for "dark thriller" — 39.9% of the catalogue has no
    genre (`test_genre_is_not_a_hard_filter`).

Fake LLMs and fake catalogue dependencies throughout, so every test is
deterministic and none needs Ollama. The last section runs the real app.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from services.librarian import (  # noqa: E402
    MAX_STEPS,
    TOOLS,
    Librarian,
    LibrarianDeps,
    _clean,
)
from services.providers.llm import LLMResponse, LLMUnavailable, ToolCall  # noqa: E402


def _book(id_, title, author="Some Author", genre="Fiction", language="English", **extra):
    return {
        "id": id_, "title": title, "author": author, "genre": genre,
        "language": language, "rating": 4.0035747133, "published_year": 2012,
        "description": "No description available.", "price": None,
        "availability": "unknown", **extra,
    }


CATALOGUE = {
    1: _book(1, "Notes on Grief", "Chimamanda Ngozi Adichie"),
    2: _book(2, "Atomic Habits", "James Clear"),
    3: _book(3, "The Grief Manual", "Someone Else"),
    4: _book(4, "Freies Denken", "Hans Meier", language="German"),
    5: _book(5, "Untitled Ledger", "Nobody", language="Unknown", genre="Unknown"),
    6: _book(6, "Free Reading", "Public Domain", price=0.0, availability="free_public_domain"),
    7: _book(7, "Costly Volume", "Rich Author", price=35.0, availability="listed"),
}


class FakeDeps:
    def __init__(self, library=None):
        self.library = library or {}
        self.searches = []
        self.library_calls = []

    def build(self) -> LibrarianDeps:
        def search(query, pool):
            self.searches.append(query)
            return [{"pk": 100 + i, "similarity": 0.9 - i / 100} for i in CATALOGUE]

        def library_for(account_id):
            self.library_calls.append(account_id)
            return set(self.library.get(account_id, set()))

        return LibrarianDeps(
            search=search,
            book_for_pk=lambda pk: CATALOGUE.get(pk - 100),
            book_by_id=lambda i: CATALOGUE.get(i),
            library_for=library_for,
        )


class Script:
    """An LLM that plays back a fixed list of responses and records what it
    was sent."""

    def __init__(self, *responses):
        self.name = "script"
        self.responses = list(responses)
        self.sent = []

    def chat(self, messages, *, tools=None):
        self.sent.append((list(messages), tools))
        return self.responses.pop(0)


def call(name, **arguments):
    return ToolCall(id="", name=name, arguments=arguments)


def calling(*tool_calls):
    return LLMResponse(content="", tool_calls=list(tool_calls), model="script")


def saying(text):
    return LLMResponse(content=text, model="script")


def run(llm, message="find me something", *, deps=None, account_id=None, profile=None):
    return Librarian(llm, (deps or FakeDeps()).build()).answer(
        message, account_id=account_id, profile=profile
    )


# -- the loop -------------------------------------------------------------------


def test_books_come_from_tool_results_and_the_caption_is_kept():
    llm = Script(
        calling(call("search_catalog", query="grief")),
        saying('Try "Notes on Grief" by Chimamanda Ngozi Adichie.'),
    )
    result = run(llm)

    assert result.grounded
    assert [b["title"] for b in result.books] == ["Notes on Grief"]
    assert result.message.startswith("Try")


def test_the_tool_result_is_actually_fed_back_to_the_model():
    llm = Script(calling(call("search_catalog", query="grief")), saying("ok"))
    run(llm)

    second_call_messages = llm.sent[1][0]
    tool_message = next(m for m in second_call_messages if m["role"] == "tool")
    payload = json.loads(tool_message["content"])
    assert payload["count"] > 0 and payload["results"][0]["title"]


def test_cards_only_include_books_the_prose_actually_mentions():
    llm = Script(
        calling(call("search_catalog", query="anything")),
        saying('I would start with "Notes on Grief".'),
    )
    result = run(llm)

    assert len(result.books) == 1, "cards for books the caption never mentions"


def test_a_model_that_never_calls_a_tool_returns_no_books():
    result = run(Script(saying("Hello! How can I help you find a book?")))

    assert result.books == [] and result.grounded


def test_the_loop_always_ends_in_prose_even_if_the_model_never_stops_calling_tools():
    looping = [calling(call("search_catalog", query="x")) for _ in range(MAX_STEPS)]
    llm = Script(*looping, saying("Here is a summary."))
    result = run(llm)

    assert len(llm.sent) == MAX_STEPS + 1
    assert llm.sent[-1][1] is None, "the closing call must offer no tools"
    assert result.message == "Here is a summary."


# -- grounding: the property this file exists for ------------------------------------


def test_a_book_named_from_memory_is_rejected_and_replaced_by_tool_data():
    llm = Script(
        calling(call("search_catalog", query="grief")),
        saying('You might enjoy "The Year of Magical Thinking" by Joan Didion.'),
    )
    result = run(llm)

    assert not result.grounded
    assert "Year of Magical Thinking" not in result.message
    assert "Notes on Grief" in result.message, "the fallback must be built from tool data"
    assert all(b["id"] in CATALOGUE for b in result.books)


def test_a_book_the_reader_named_is_not_mistaken_for_an_invention():
    """Q1 in the first real run flagged 'Atomic Habits' — quoted straight back
    from the reader's own message."""
    # "Dune" is deliberately NOT in the fake catalogue: were it, the title
    # guard would accept it as a returned book and this test would prove
    # nothing about the reader-named exemption.
    llm = Script(
        calling(call("search_catalog", query="epic desert planet")),
        saying('Similar to "Dune Messiah", try "Notes on Grief".'),
    )
    result = run(llm, message="something like Dune Messiah")

    assert result.grounded
    assert all(b["id"] in CATALOGUE for b in result.books)


def test_an_invented_price_is_rejected():
    llm = Script(
        calling(call("search_catalog", query="statistics")),
        saying('"Notes on Grief" has a price tag of $35.00 and is currently available.'),
    )
    result = run(llm)

    assert not result.grounded
    assert "$35" not in result.message


def test_a_price_check_admits_the_price_it_returned():
    llm = Script(
        calling(call("search_catalog", query="costly")),
        calling(call("check_availability", book_id=7)),
        saying('"Costly Volume" costs $35.00.'),
    )
    result = run(llm)

    assert result.grounded, result.message


def test_price_wording_without_a_price_check_is_rejected_even_without_a_number():
    llm = Script(
        calling(call("search_catalog", query="grief")),
        saying('"Notes on Grief" is currently available.'),
    )

    assert not run(llm).grounded


def test_an_invented_year_is_rejected_and_a_returned_one_is_not():
    bad = Script(
        calling(call("search_catalog", query="grief")),
        saying('"Notes on Grief" was published in 1974.'),
    )
    good = Script(
        calling(call("search_catalog", query="grief")),
        saying('"Notes on Grief" was published in 2012.'),
    )

    assert not run(bad).grounded
    assert run(good).grounded


def test_the_model_is_never_shown_raw_scores_it_could_repeat_to_the_reader():
    """Found by reading real output: 'rated 4.0035747133', 'similarity score of
    0.753'."""
    llm = Script(calling(call("search_catalog", query="grief")), saying("ok"))
    run(llm)

    payload = json.loads(next(m for m in llm.sent[1][0] if m["role"] == "tool")["content"])
    first = payload["results"][0]
    assert "similarity" not in first
    assert first["rating"] == 4.0


def test_the_stray_arrow_artefact_is_stripped_and_ordinary_text_is_not():
    assert _clean('">To Kill a Mockingbird" by Harper Lee') == '"To Kill a Mockingbird" by Harper Lee'
    assert _clean('He said "hello > world" today') == 'He said "hello > world" today'


# -- search_catalog semantics ----------------------------------------------------------


def _search(**arguments):
    llm = Script(calling(call("search_catalog", **arguments)), saying("done"))
    run(llm)
    return json.loads(next(m for m in llm.sent[1][0] if m["role"] == "tool")["content"])


def test_exclude_authors_removes_that_author_and_only_that_author():
    payload = _search(query="habits", exclude_authors=["James Clear"])
    authors = {r["author"] for r in payload["results"]}

    assert "James Clear" not in authors and "Chimamanda Ngozi Adichie" in authors


def test_a_confirmed_other_language_is_excluded_but_unknown_is_not():
    """F-47's rule, applied here: absence of a language label is not evidence
    the book is not English."""
    titles = {r["title"] for r in _search(query="anything")["results"]}

    assert "Freies Denken" not in titles
    assert "Untitled Ledger" in titles


def test_language_any_disables_the_language_rule():
    titles = {r["title"] for r in _search(query="anything", language="any")["results"]}

    assert "Freies Denken" in titles


def test_genre_is_not_a_hard_filter():
    """The model offered `genre: "thriller"`; matching it as a hard filter
    against a catalogue that is 39.9% genre-Unknown returned nothing for 'dark
    thriller'. The parameter is gone from the schema, and an argument that
    sneaks in anyway must not filter."""
    schema = next(t for t in TOOLS if t["function"]["name"] == "search_catalog")
    assert "genre" not in schema["function"]["parameters"]["properties"]

    payload = _search(query="dark thriller", genre="thriller")
    assert payload["count"] > 0


def test_a_placeholder_description_is_never_offered_as_a_summary():
    payload = _search(query="anything")

    assert all("summary" not in r for r in payload["results"])


# -- identity: the tools cannot be asked about anyone else ------------------------------


def test_no_tool_accepts_a_user_id():
    for tool in TOOLS:
        properties = tool["function"]["parameters"].get("properties", {})
        assert not [p for p in properties if "user" in p.lower()], tool["function"]["name"]


def test_a_user_id_smuggled_into_arguments_does_not_change_whose_library_is_read():
    deps = FakeDeps(library={7: {1}, 99: {3}})
    llm = Script(
        calling(
            call("check_user_library", user_id=99),
            call("search_catalog", query="grief", exclude_read=True, user_id=99),
        ),
        saying("ok"),
    )
    run(llm, deps=deps, account_id=7)

    # Both tools that read a library, both offered someone else's id.
    assert deps.library_calls == [7, 7]


def test_an_anonymous_reader_has_no_library_and_exclude_read_is_a_no_op():
    deps = FakeDeps(library={7: {1}})
    llm = Script(
        calling(call("check_user_library"), call("search_catalog", query="grief", exclude_read=True)),
        saying("ok"),
    )
    run(llm, deps=deps, account_id=None)
    tool_messages = [m for m in llm.sent[1][0] if m["role"] == "tool"]

    assert json.loads(tool_messages[0]["content"])["logged_in"] is False
    assert json.loads(tool_messages[1]["content"])["count"] > 0
    assert deps.library_calls == []


def test_exclude_read_drops_exactly_the_books_in_the_readers_library():
    deps = FakeDeps(library={7: {1}})
    llm = Script(calling(call("search_catalog", query="grief", exclude_read=True)), saying("ok"))
    run(llm, deps=deps, account_id=7)
    payload = json.loads(next(m for m in llm.sent[1][0] if m["role"] == "tool")["content"])

    assert 1 not in {r["id"] for r in payload["results"]}
    assert 3 in {r["id"] for r in payload["results"]}


# -- a model's mistakes are the model's to correct ----------------------------------------


def test_malformed_and_unknown_tool_calls_come_back_as_errors_not_exceptions():
    llm = Script(
        calling(
            call("check_availability", book_id="not-a-number"),
            call("no_such_tool"),
            call("search_catalog"),
        ),
        saying("sorry"),
    )
    result = run(llm)
    errors = [json.loads(m["content"]) for m in llm.sent[1][0] if m["role"] == "tool"]

    assert all("error" in e for e in errors)
    assert result.message == "sorry"


def test_llm_unavailable_propagates_so_the_engine_can_fall_back():
    class Dead:
        name = "dead"

        def chat(self, messages, *, tools=None):
            raise LLMUnavailable("down")

    with pytest.raises(LLMUnavailable):
        run(Dead())


# -- the real app ---------------------------------------------------------------------------


@pytest.fixture
def real_app(fitted_app):
    main, client = fitted_app
    engine = main.RECOMMENDER.chatbot
    original = engine.librarian
    yield main, client, engine
    engine.librarian = original


class CitesFirstResult:
    """Searches, then quotes whatever the real catalogue search returned first.
    Deterministic, but every book in the answer comes from the real tool."""

    name = "cites-first"

    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1
        if self.calls == 1:
            return calling(call("search_catalog", query="dark thriller"))
        tool = json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])
        first = tool["results"][0]
        return saying(f'Try "{first["title"]}" by {first["author"]}.')


def test_the_real_app_returns_real_catalogue_books(real_app):
    main, client, engine = real_app
    engine.librarian.llm = CitesFirstResult()

    body = client.post("/api/chat", json={"user_id": "t", "message": "recommend me a dark thriller"}).json()

    assert body["mode"] == "llm" and body["llm"]["grounded"]
    assert body["recommendations"], "the librarian returned no books"
    for book in body["recommendations"]:
        assert main.BOOK_BY_ID[book["id"]]["title"] == book["title"]


def test_the_real_app_falls_back_to_the_classifier_when_every_model_is_down(real_app):
    """Section 12: never a hard crash. The old behaviour, exactly, plus an
    honest `mode` saying which path answered."""
    main, client, engine = real_app

    class Down:
        name = "down"

        def chat(self, messages, *, tools=None):
            raise LLMUnavailable("all models down")

    engine.librarian.llm = Down()
    response = client.post("/api/chat", json={"user_id": "t", "message": "recommend a thriller"})
    body = response.json()

    assert response.status_code == 200
    assert body["mode"] == "classifier"
    assert body["intent"] == "recommend" and body["answer"]


def test_the_engine_runs_classifier_only_when_the_librarian_is_switched_off(real_app):
    main, client, engine = real_app
    engine.librarian = None

    body = client.post("/api/chat", json={"user_id": "t", "message": "hello"}).json()

    assert body["mode"] == "classifier"


def test_a_forged_token_on_the_chat_route_is_rejected_not_treated_as_anonymous(real_app):
    """The route now reads the caller's account (for the library tool), so it
    inherits OI-10: a bad token is an error, not an anonymous visit."""
    main, client, engine = real_app
    engine.librarian = None

    response = client.post(
        "/api/chat", json={"message": "hello"}, headers={"Authorization": "Bearer nonsense"}
    )

    assert response.status_code == 401


# -- conversation history (Phase D) ----------------------------------------------------


def test_prior_turns_are_sent_to_the_model_before_the_new_question():
    llm = Script(saying("ok"))
    history = [
        {"role": "user", "content": "something about grief"},
        {"role": "assistant", "content": 'Try "Notes on Grief".'},
    ]
    Librarian(llm, FakeDeps().build()).answer(
        "something shorter?", history=history
    )
    roles = [(m["role"], m["content"]) for m in llm.sent[0][0]]

    assert roles[0][0] == "system"
    assert roles[1:3] == [(h["role"], h["content"]) for h in history]
    assert roles[-1] == ("user", "something shorter?")


def test_no_history_still_sends_system_then_question():
    llm = Script(saying("ok"))
    run(llm, message="hello")

    assert [m["role"] for m in llm.sent[0][0]] == ["system", "user"]


def test_tool_calls_from_earlier_turns_are_not_replayed_as_context():
    """A stale search result read as current is exactly the ungrounded fact the
    guards exist to stop, so history carries prose only."""
    llm = Script(saying("ok"))
    Librarian(llm, FakeDeps().build()).answer(
        "and shorter?",
        history=[{"role": "user", "content": "grief"}, {"role": "assistant", "content": "a"}],
    )

    assert all("tool_calls" not in m and m["role"] != "tool" for m in llm.sent[0][0])


def test_a_book_grounded_in_an_earlier_turn_is_not_flagged_as_invented():
    """Found by a live multi-turn probe, not by reasoning: "who wrote the
    first one?" was rejected three times running, because ctx.seen only knew
    this turn's tool results. The book was real and *was* grounded when it was
    produced — a turn earlier."""
    llm = Script(saying('"Notes on Grief" is by Chimamanda Ngozi Adichie.'))
    result = Librarian(llm, FakeDeps().build()).answer(
        "who wrote the first one?",
        history=[{"role": "assistant", "content": 'Try "Notes on Grief".'}],
        prior_book_ids=[1],
    )

    assert result.grounded, result.message
    assert [b["title"] for b in result.books] == ["Notes on Grief"]


def test_carrying_a_book_forward_does_not_excuse_inventing_a_new_one():
    """The carry-forward widens what counts as grounded; it must not blank the
    guard. A title no tool ever returned is still rejected."""
    llm = Script(saying('Also try "The Year of Magical Thinking".'))
    result = Librarian(llm, FakeDeps().build()).answer(
        "anything else?", prior_book_ids=[1]
    )

    assert not result.grounded


def test_only_real_catalogue_ids_are_carried_forward():
    """A stored id that no longer resolves (book removed, stale conversation)
    must be skipped, not crash the turn."""
    llm = Script(saying("Nothing to add."))
    result = Librarian(llm, FakeDeps().build()).answer(
        "hello", prior_book_ids=[1, 999_999]
    )

    assert result.grounded
