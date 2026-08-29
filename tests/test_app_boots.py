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

ATTACKS = [
    {"book_name": "Animal Farm", "output_file": "../server.js"},
    {"book_id": 1, "output_file": "../../server.js"},
    {"book_id": 1, "output_file": "/etc/passwd"},
]


@pytest.mark.parametrize("payload", ATTACKS)
def test_exploit_is_rejected_and_server_js_untouched(client, payload):
    target = ROOT / "server.js"
    before = hashlib.md5(target.read_bytes()).hexdigest()

    r = client.post("/api/audiobook/generate", json=payload)

    assert r.status_code == 422, f"exploit was not rejected: {payload}"
    after = hashlib.md5(target.read_bytes()).hexdigest()
    assert before == after, "server.js was modified — F-04 is open again"


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


@pytest.mark.xfail(
    reason="F-22: ChatbotEngine.respond never populates response['books'] "
    "for any intent — the chatbot is an intent classifier with canned "
    "replies. Pre-existing; tracked for PR 2.",
    strict=True,
)
def test_chatbot_returns_recommendations(client):
    r = client.post(
        "/api/chat", json={"user_id": "t", "message": "recommend me a dark thriller"}
    )
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


def test_search_readiness_is_known_before_the_first_search(client):
    """It is warmed at startup. If it only loaded on first use, /health would
    report `false` until somebody searched — a health check that lies until
    traffic arrives is worse than one that says nothing.
    """
    import main

    assert (main._SEARCH_ENCODER is not None) or (main._SEARCH_ENCODER_ERROR is not None), (
        "startup neither loaded the encoder nor recorded why not"
    )
