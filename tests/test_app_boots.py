"""End-to-end tests against a real, fully-fitted app.

These are the "true form" tests that tests/README.md called for. They need
the full stack (scikit-learn, gtts, plyer) installed, and they boot the real
FastAPI app with the real 29,975-book catalogue, so the module is skipped
rather than failed when those are absent.

The source-text assertions in test_security.py and test_data_loading.py stay
as a cheap first line of defence that runs anywhere. These prove the app
actually works.
"""

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"

# main.py uses `from engine import ...`, so backend/ must be importable.
sys.path.insert(0, str(BACKEND))

pytest.importorskip("sklearn", reason="full ML stack not installed")
pytest.importorskip("gtts", reason="full ML stack not installed")
fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture(scope="module")
def client():
    """Boot the real app once. Fitting the ML engine takes a few seconds."""
    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c


# --------------------------------------------------------------------------
# The app boots and reports itself honestly
# --------------------------------------------------------------------------

def test_health_is_ok_and_honest(client):
    h = client.get("/health").json()
    assert h["ok"] is True
    assert h["books_loaded"] == 29975, "the real catalogue did not load (F-03)"
    assert h["data_source"] == "json"
    assert h["using_real_data"] is True


def test_ml_engine_actually_fits(client):
    """The pandas 3 / Arrow regression that broke the whole engine.

    df['genre'].astype(str).values returns an ArrowStringArray under pandas 3,
    which has no .flatten(), so fit_transform raised and every ML feature was
    silently disabled behind a green /health.
    """
    h = client.get("/health").json()
    for engine in (
        "ml_ready",
        "audiobook_ready",
        "comments_ready",
        "chatbot_ready",
        "reminder_ready",
        "questioner_ready",
    ):
        assert h[engine] is True, f"{engine} is False — the ML fit failed"


def test_health_does_not_500_when_engine_is_ready(client):
    """F-21: /health did int() on a ClusteringModel and raised whenever the
    engine had fitted — it only returned 200 while the app was broken."""
    r = client.get("/health")
    assert r.status_code == 200
    assert isinstance(r.json()["clusters"], int)


# --------------------------------------------------------------------------
# F-04 — the true form of the security test
# --------------------------------------------------------------------------

# The payloads still name `server.js`, the file the original exploit
# destroyed, because they are attacker-supplied strings and a string does not
# need its target to exist. The *canary* had to change: OI-5 retired Express
# and deleted that file, so a hash of it is no longer a hash of anything.
# `docker-compose.yml` replaces it — tracked, at the project root, one `../`
# out of the audio directory, and loudly wrong if it ever changes.
CANARY = "docker-compose.yml"

ATTACKS = [
    {"book_name": "Animal Farm", "output_file": "../server.js"},
    {"book_id": 1, "output_file": "../../server.js"},
    {"book_id": 1, "output_file": "/etc/passwd"},
    {"book_id": 1, "output_file": f"../{CANARY}"},
    {"book_id": 1, "output_file": f"../../{CANARY}"},
]


@pytest.mark.parametrize("payload", ATTACKS)
def test_exploit_is_rejected_and_the_canary_untouched(client, payload):
    target = ROOT / CANARY
    assert target.exists(), f"the canary {CANARY} is gone; this test proves nothing"
    before = hashlib.md5(target.read_bytes()).hexdigest()

    r = client.post("/api/audiobook/generate", json=payload)

    assert r.status_code == 422, f"exploit was not rejected: {payload}"
    after = hashlib.md5(target.read_bytes()).hexdigest()
    assert before == after, f"{CANARY} was modified — F-04 is open again"


# --------------------------------------------------------------------------
# F-16 — the route that was shadowed into unreachability
# --------------------------------------------------------------------------

def test_filter_options_is_reachable(client):
    r = client.get("/api/books/filter-options")
    assert r.status_code == 200, (
        "422 means /api/books/{book_id} is shadowing this route again"
    )
    body = r.json()
    # The move must have preserved the original response shape.
    assert {"genres", "authors", "moods", "languages", "clusters"} <= set(body)


# --------------------------------------------------------------------------
# Core flows return real books
# --------------------------------------------------------------------------

def _titles(payload):
    return [str(b.get("title", "")) for b in (payload.get("items") or [])]


def test_recommendations_are_real_books(client):
    r = client.post("/api/recommend", json={"user_id": "t", "top_k": 5})
    assert r.status_code == 200
    titles = _titles(r.json())
    assert titles, "no recommendations returned"
    assert not any(t.startswith("Book 0") for t in titles), (
        "synthetic data is being served (F-03)"
    )


def test_questionnaire_returns_real_books(client):
    r = client.post(
        "/api/questionnaire",
        json={"user_id": "t", "genre": "fantasy", "mood": "dark", "top_k": 3},
    )
    assert r.status_code == 200
    titles = _titles(r.json())
    assert titles, "cold-start questionnaire returned nothing"
    assert not any(t.startswith("Book 0") for t in titles)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/api/books?limit=5", None),
        ("get", "/api/books/1", None),
        ("get", "/api/search?q=harry", None),
        ("get", "/api/clusters", None),
        ("post", "/api/chat", {"user_id": "t", "message": "recommend a thriller"}),
        ("get", "/api/comments/1", None),
    ],
)
def test_live_endpoints_respond(client, method, path, body):
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 200, f"{method.upper()} {path} -> {r.status_code}"


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("post", "/api/comments", {"book_id": 1, "comment": "anon", "rating": 5}),
        ("delete", "/api/comments/1", None),
        # F-07 read-authz sweep (PR 3): these leaked other users' data.
        ("post", "/api/progress", {"book_id": 1, "progress": 0.5}),
        ("get", "/api/progress", None),
        ("post", "/api/reminder", {"book_id": 1, "enabled": True}),
        ("get", "/api/reminders", None),
        ("get", "/api/profile/1", None),
    ],
)
def test_write_endpoints_require_auth(client, method, path, body):
    """These returned 200 to anonymous callers before PR 2 (F-07).

    Kept alongside the anonymous-read smoke tests above so the boundary
    between "public read" and "authenticated write" stays explicit.
    """
    r = getattr(client, method)(path, **({"json": body} if body else {}))
    assert r.status_code == 401, f"{method.upper()} {path} is still open to anonymous callers"


def test_audiobook_stream_404s_for_ungenerated_book(client):
    """F-06: this used to serve one global audiobook.mp3 for every book."""
    r = client.get("/api/audiobook/1/stream")
    assert r.status_code == 404


def test_chatbot_returns_recommendations(client):
    """F-22, was xfail(strict=True) while the chatbot was an intent classifier
    with canned replies. Now driven through the real app with a scripted model
    (deterministic — the live model is exercised separately in
    tests/test_librarian.py and by hand): the model asks the real catalogue
    search for books and quotes what comes back.

    Pulled forward from Phase E because Phase C is exactly the change that
    flips this test — leaving a strict xfail in place would have failed the
    suite the moment the fix worked, which is what strict is for."""
    import json

    import main
    from services.providers.llm import LLMResponse, ToolCall

    class SearchesThenQuotes:
        name = "scripted"
        calls = 0

        def chat(self, messages, *, tools=None):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="", tool_calls=[
                    ToolCall(id="", name="search_catalog", arguments={"query": "dark thriller"})
                ])
            tool = json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])
            return LLMResponse(content=f'Try "{tool["results"][0]["title"]}".')

    engine = main.RECOMMENDER.chatbot
    original = engine.librarian.llm
    engine.librarian.llm = SearchesThenQuotes()
    try:
        r = client.post(
            "/api/chat", json={"user_id": "t", "message": "recommend me a dark thriller"}
        )
    finally:
        engine.librarian.llm = original
    assert r.json()["recommendations"], "chatbot returned no books"


def test_health_reports_search_readiness_without_gating_on_it(client):
    """F-03 and F-21's lesson applied to a new capability.

    A capability whose state nobody can see is one that fails silently. But
    semantic search degrades cleanly to a 503 on its own endpoint, so folding
    it into `ok` would take the whole service red over one optional feature.
    `search_ready: false` beside `ok: true` is the honest shape.
    """
    body = client.get("/api/health").json()

    assert "search_ready" in body, "search state is invisible in /health"
    assert isinstance(body["search_ready"], bool)
    assert "search_backend" in body and "search_error" in body

    if body["search_ready"]:
        assert body["search_backend"], "ready but nameless"
        assert body["search_error"] is None
    else:
        # Not ready is allowed; silently not ready is not.
        assert body["search_error"], "search is unavailable and says nothing"


# --------------------------------------------------------------------------
# F-50 — the questionnaire page's own options endpoint now exists
# --------------------------------------------------------------------------

def test_questionnaire_options_is_reachable(client):
    """frontend/js/questionnair.js has always fetched this path and 404'd
    silently, falling back to its own hardcoded questions. This is the real
    endpoint, not a client-side patch."""
    r = client.get("/api/questionnaire/options")
    assert r.status_code == 200
    questions = r.json()["questions"]
    ids = {q["id"] for q in questions}
    assert {"genre", "mood", "pace", "language", "popularity", "favorite_author"} <= ids


def test_questionnaire_options_values_are_the_ones_the_engine_understands():
    """The values are not just UI copy — mood, pace and popularity are
    matched against exact vocabularies downstream (MOOD_NORMALISE,
    PACE_PAGE_MAP, the literal popular/underrated check). An option here
    that isn't in those vocabularies would silently fall through to "any"
    rather than error — the same silent-drift shape as F-16."""
    import sys

    sys.path.insert(0, str(BACKEND))
    import main  # noqa: F401 — must import before api.questionnaire, or api.questionnaire's own `import main` triggers a circular re-entry into main.py before this module has defined `router`
    from domain.entities import MOOD_GENRE_MAP
    from services.recommendation import QuestionerEngine

    from api.questionnaire import questionnaire_options

    questions = {q["id"]: q["options"] for q in questionnaire_options()["questions"]}

    for mood in questions["mood"]:
        if mood != "any":
            assert mood in MOOD_GENRE_MAP, f"{mood!r} is not a real mood key"

    for pace in questions["pace"]:
        assert pace in QuestionerEngine.PACE_PAGE_MAP or pace == "any"


def test_questionnaire_options_genre_actually_exists_in_the_catalogue(client):
    """Offered genres come from the live catalogue, not a guessed list that
    might not exist in the data at all.

    Checked against main.BOOKS directly rather than /api/books/filter-options:
    that endpoint sorts alphabetically and caps at 200 of what turns out to be
    thousands of distinct raw genre/shelf strings, so common genres like
    "Fiction" are not guaranteed to survive its truncation — a mismatch there
    would be a fact about that endpoint's own trade-off, not about whether
    this option list is grounded in real data."""
    import main

    real_lower = {str(b.get("genre", "")).strip().lower() for b in main.BOOKS}

    r = client.get("/api/questionnaire/options")
    genres = next(q["options"] for q in r.json()["questions"] if q["id"] == "genre")
    assert genres and genres[-1] == "any"
    for g in genres[:-1]:
        assert g.lower() in real_lower, f"{g!r} does not appear in the real catalogue"


# --------------------------------------------------------------------------
# Waiting-on-you #3 — /api/feedback no longer claims a taste model
# --------------------------------------------------------------------------

def test_feedback_does_not_claim_a_taste_model(client):
    """taste_vector is never assigned anywhere (F-46), so "taste model
    updated" and taste_vector_dim described a feature that does not exist."""
    books = client.get("/api/books?limit=1").json()["items"]
    book_id = books[0]["id"]

    r = client.post(
        "/api/feedback", json={"user_id": "t", "book_id": book_id, "rating": 4}
    )
    assert r.status_code == 200
    body = r.json()
    assert "taste model" not in body["message"].lower()
    assert "taste_vector_dim" not in body


# --------------------------------------------------------------------------
# F-34 — an invalid token is rejected wherever OptionalUser is used, not
# just on the search endpoint it was originally found on
# --------------------------------------------------------------------------

def test_an_invalid_token_401s_on_every_optionaluser_route_not_just_search(client):
    """get_current_user_optional is the single shared dependency behind
    OptionalUser (auth.py), already fixed for OI-10/F-34 on the search
    endpoint. This proves the fix is at the shared dependency, not
    per-route, by hitting a different OptionalUser route directly."""
    r = client.get(
        "/api/books/1", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


def test_search_readiness_is_known_before_the_first_search(client):
    """It is warmed at startup. If it only loaded on first use, /health would
    report `false` until somebody searched — a health check that lies until
    traffic arrives is worse than one that says nothing.
    """
    import main

    assert (main._SEARCH_ENCODER is not None) or (main._SEARCH_ENCODER_ERROR is not None), (
        "startup neither loaded the encoder nor recorded why not"
    )


# --------------------------------------------------------------------------
# F-54 — fields that reported a measurement that never happened
# --------------------------------------------------------------------------

DEAD_FIELDS = ("cf_sim", "content_sim")


def test_the_api_does_not_report_scores_it_never_computes(client):
    """`cf_sim` and `content_sim` were read in `_to_api` from keys nothing
    ever assigned, so `_safe_float(None)` returned 0.0 for every book in
    every response since the ranker was written. A constant dressed as a
    measurement is worse than a missing field: a caller can see a missing
    field, and cannot see that 0.0 means "not measured" rather than "no
    similarity".

    Removed rather than populated — nothing read them, and filling them from
    `content_s`/`genre_pop_s` would have been building a feature to justify a
    bug. This asserts they stay gone.
    """
    r = client.post("/api/recommend", json={"user_id": "f54", "top_k": 5})
    assert r.status_code == 200, r.text
    body = r.json()
    books = body.get("items") or []
    assert books, f"no recommendations to check: {str(body)[:200]}"

    for book in books:
        for field in DEAD_FIELDS:
            assert field not in book, (
                f"{field} is back in the API payload. If it is being populated "
                "for real now, this test should assert it varies; if not, it is "
                "F-54 again."
            )


def test_the_dead_fields_are_not_being_produced_anywhere():
    """The endpoint test above only covers the shape it happens to return.
    `_gutenberg_to_api` builds a second shape for a source that needs no
    catalogue row, and it carried the same two fields — set to `None` rather
    than 0.0, so the two payloads did not even agree with each other."""
    import re

    src = (ROOT / "backend" / "services" / "recommendation.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    for field in DEAD_FIELDS:
        assert not re.search(rf'"{field}"\s*:', code), (
            f"{field} is being produced again in recommendation.py"
        )
