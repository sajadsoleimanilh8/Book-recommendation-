"""Rate limiting — OI-5, the blocking deployment gate.

`POST /api/audiobook/generate` is unauthenticated and synchronous: each call
fetches a book and synthesises speech in the request thread, so a few repeat
calls occupy every worker. It is a denial-of-service lever that needs no
credentials, which is why OI-5 blocks deployment.

These tests cover the limiter itself and the endpoint that uses it. The
endpoint test matters separately: a correct limiter that the route forgets to
call protects nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import ratelimit  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def in_process(monkeypatch):
    """Force the in-process path so the test does not depend on Redis."""
    monkeypatch.setattr(ratelimit, "_get_redis", lambda: None)


# -- the limiter -----------------------------------------------------------


def test_requests_under_the_limit_pass(in_process):
    for _ in range(3):
        ratelimit.hit("k", limit=3, window=60)


def test_the_request_over_the_limit_is_refused(in_process):
    for _ in range(3):
        ratelimit.hit("k", limit=3, window=60)
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.hit("k", limit=3, window=60)


def test_one_client_cannot_exhaust_anothers_budget(in_process):
    """A limiter keyed on something shared would let one caller lock out
    everybody else — a denial of service delivered by the defence."""
    for _ in range(3):
        ratelimit.hit("ip:1.1.1.1", limit=3, window=60)
    ratelimit.hit("ip:2.2.2.2", limit=3, window=60)  # must not raise


def test_the_window_expires(in_process, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: clock[0])

    for _ in range(3):
        ratelimit.hit("k", limit=3, window=60)
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.hit("k", limit=3, window=60)

    clock[0] += 61
    ratelimit.hit("k", limit=3, window=60)  # window has rolled over


def test_refusal_says_how_long_to_wait(in_process):
    for _ in range(3):
        ratelimit.hit("k", limit=3, window=60)
    with pytest.raises(ratelimit.RateLimited) as caught:
        ratelimit.hit("k", limit=3, window=60)
    assert 0 < caught.value.retry_after <= 61


def test_a_redis_outage_degrades_instead_of_failing_open(monkeypatch):
    """Redis is most likely to be down under exactly the load a rate limit
    exists to survive. Failing open there means no protection when it is
    needed most."""

    class Broken:
        def pipeline(self):
            raise ConnectionError("redis is gone")

    monkeypatch.setattr(ratelimit, "_get_redis", lambda: Broken())

    for _ in range(3):
        ratelimit.hit("k", limit=3, window=60)
    with pytest.raises(ratelimit.RateLimited):
        ratelimit.hit("k", limit=3, window=60)


def test_forwarded_headers_are_ignored(in_process):
    """X-Forwarded-For is attacker-controlled with no trusted proxy in front.
    Honouring it would let anyone reset their own limit with one header, which
    is worse than no limiter because it reads as protection.
    """

    class FakeClient:
        host = "10.0.0.1"

    class FakeRequest:
        client = FakeClient()
        headers = {"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}

    assert ratelimit.client_ip(FakeRequest()) == "10.0.0.1"


# -- the endpoint ----------------------------------------------------------

BODY = {"book_id": 1, "book_name": "Animal Farm", "language": "en"}


@pytest.fixture
def endpoint(fitted_app, in_process, monkeypatch):
    """The app with speech synthesis stubbed out.

    Not an optimisation. The real `generate` performs an unbounded network
    fetch and TTS call in the request thread with no timeout, so the first
    version of these tests hung the suite past 120 seconds on three calls.
    That is F-19 demonstrating itself: the reason this endpoint needs both a
    rate limit *and* a background job. Stubbing keeps these tests about the
    limiter; the hang is recorded as evidence, not designed around.
    """
    main_module, client = fitted_app
    if not getattr(main_module.RECOMMENDER, "audiobook", None):
        pytest.skip("audiobook engine unavailable")

    calls: list[dict] = []
    monkeypatch.setattr(
        main_module.RECOMMENDER.audiobook,
        "generate",
        lambda **kw: (calls.append(kw), {"ok": True})[1],
    )
    return client, calls


def test_the_audiobook_endpoint_actually_enforces_it(endpoint):
    """A correct limiter the route never calls protects nothing."""
    client, _ = endpoint
    statuses = [
        client.post("/api/audiobook/generate", json=BODY).status_code
        for _ in range(ratelimit.AUDIOBOOK_LIMIT + 2)
    ]

    assert 429 in statuses, f"never rate limited: {statuses}"
    # The refusals must come after the allowance, not instead of it.
    assert statuses.index(429) >= ratelimit.AUDIOBOOK_LIMIT


def test_the_429_carries_retry_after(endpoint):
    client, _ = endpoint
    for _ in range(ratelimit.AUDIOBOOK_LIMIT + 2):
        response = client.post("/api/audiobook/generate", json=BODY)
        if response.status_code == 429:
            assert response.headers.get("Retry-After"), "no Retry-After header"
            assert int(response.headers["Retry-After"]) > 0
            return
    pytest.fail("endpoint never returned 429")


def test_the_limit_runs_before_any_work(endpoint):
    """Rejecting after generation has started costs exactly what the limit
    exists to prevent."""
    client, calls = endpoint
    for _ in range(ratelimit.AUDIOBOOK_LIMIT + 3):
        client.post("/api/audiobook/generate", json=BODY)

    assert len(calls) <= ratelimit.AUDIOBOOK_LIMIT, (
        f"generation ran {len(calls)} times for a limit of "
        f"{ratelimit.AUDIOBOOK_LIMIT}"
    )


# -- POST /api/chat --------------------------------------------------------
#
# F-22 made this the more expensive of the two limited endpoints, and it was
# unlimited until OI-5: `OptionalUser` means anonymous callers are supported
# by design, and one request drives up to MAX_STEPS tool-calling round trips
# against a local model on one GPU.

CHAT_BODY = {"message": "recommend me something about grief", "user_id": "guest"}


@pytest.fixture
def chat(fitted_app, in_process, monkeypatch):
    """The app with the chatbot engine stubbed out.

    The real `respond` reaches Ollama. These tests are about whether the
    route refuses, which must hold whether or not a model is running.
    """
    main_module, client = fitted_app
    if not getattr(main_module.RECOMMENDER, "chatbot", None):
        pytest.skip("chatbot engine unavailable")

    calls: list[dict] = []
    monkeypatch.setattr(
        main_module.RECOMMENDER.chatbot,
        "respond",
        lambda **kw: (calls.append(kw), {"message": "stub", "books": []})[1],
    )
    return client, calls


def test_the_chat_endpoint_actually_enforces_it(chat):
    client, _ = chat
    statuses = [
        client.post("/api/chat", json=CHAT_BODY).status_code
        for _ in range(ratelimit.CHAT_LIMIT + 2)
    ]

    assert 429 in statuses, f"never rate limited: {statuses}"
    assert statuses.index(429) >= ratelimit.CHAT_LIMIT


def test_the_chatbot_alias_shares_the_same_budget(chat):
    """`/chatbot` and `/api/chat` are the same handler under two paths. A
    limit keyed on the path would be bypassed by alternating between them."""
    client, _ = chat
    for _ in range(ratelimit.CHAT_LIMIT):
        client.post("/chatbot", json=CHAT_BODY)

    assert client.post("/api/chat", json=CHAT_BODY).status_code == 429


def test_the_chat_limit_runs_before_the_model_is_touched(chat):
    """Refusing after the tool loop has run costs exactly what the limit
    exists to prevent — this endpoint's whole expense is downstream."""
    client, calls = chat
    for _ in range(ratelimit.CHAT_LIMIT + 3):
        client.post("/api/chat", json=CHAT_BODY)

    assert len(calls) <= ratelimit.CHAT_LIMIT, (
        f"the engine ran {len(calls)} times for a limit of {ratelimit.CHAT_LIMIT}"
    )


def test_the_chat_429_carries_retry_after(chat):
    client, _ = chat
    for _ in range(ratelimit.CHAT_LIMIT + 2):
        response = client.post("/api/chat", json=CHAT_BODY)
        if response.status_code == 429:
            assert int(response.headers["Retry-After"]) > 0
            return
    pytest.fail("endpoint never returned 429")


def test_the_limit_applies_before_the_engine_ready_check(fitted_app, in_process, monkeypatch):
    """An unfitted engine answers with a cheap error, but only after the
    handler has been entered. If the readiness check came first the endpoint
    would still be an unlimited target whenever the engine is down — which is
    exactly when the box is least able to absorb it."""
    main_module, client = fitted_app
    monkeypatch.setattr(main_module, "RECOMMENDER", None)

    statuses = [
        client.post("/api/chat", json=CHAT_BODY).status_code
        for _ in range(ratelimit.CHAT_LIMIT + 2)
    ]
    assert 429 in statuses, f"unlimited while the engine is down: {statuses}"


# -- what a request is charged against -------------------------------------


class _Req:
    def __init__(self, host="10.0.0.1"):
        self.client = type("C", (), {"host": host})()
        self.headers: dict = {}


def test_an_anonymous_request_is_charged_to_its_address():
    assert ratelimit.caller_keys("chat", _Req("10.0.0.1")) == [
        "ratelimit:chat:ip:10.0.0.1"
    ]


def test_signing_in_adds_a_bucket_rather_than_replacing_one():
    """The hole this avoids: key on the account *instead of* the address and
    the IP ceiling disappears for anyone willing to register, which costs an
    attacker one request."""
    keys = ratelimit.caller_keys("chat", _Req("10.0.0.1"), account_id=7)

    assert "ratelimit:chat:ip:10.0.0.1" in keys, "signing in escaped the IP ceiling"
    assert "ratelimit:chat:u:7" in keys


def test_two_accounts_on_one_address_still_share_that_address(in_process):
    """Registering a second account must not buy a second allowance from the
    same machine."""
    for i in range(ratelimit.CHAT_LIMIT):
        ratelimit.hit_all(
            ratelimit.caller_keys("chat", _Req("10.0.0.1"), account_id=1),
            ratelimit.CHAT_LIMIT, ratelimit.CHAT_WINDOW,
        )

    with pytest.raises(ratelimit.RateLimited):
        ratelimit.hit_all(
            ratelimit.caller_keys("chat", _Req("10.0.0.1"), account_id=2),
            ratelimit.CHAT_LIMIT, ratelimit.CHAT_WINDOW,
        )


def test_one_account_cannot_roam_addresses_without_limit(in_process):
    """What the second bucket is for. Each address is a fresh IP bucket, so
    the account bucket is the only thing left holding the caller."""
    for i in range(ratelimit.CHAT_LIMIT):
        ratelimit.hit_all(
            ratelimit.caller_keys("chat", _Req(f"10.0.0.{i}"), account_id=9),
            ratelimit.CHAT_LIMIT, ratelimit.CHAT_WINDOW,
        )

    with pytest.raises(ratelimit.RateLimited):
        ratelimit.hit_all(
            ratelimit.caller_keys("chat", _Req("10.0.99.99"), account_id=9),
            ratelimit.CHAT_LIMIT, ratelimit.CHAT_WINDOW,
        )


def test_the_two_endpoints_have_separate_budgets(in_process):
    """A shared bucket would let audiobook traffic lock a reader out of chat,
    and the two cost different things."""
    for _ in range(ratelimit.AUDIOBOOK_LIMIT):
        ratelimit.hit_all(
            ratelimit.caller_keys("audiobook", _Req()),
            ratelimit.AUDIOBOOK_LIMIT, ratelimit.AUDIOBOOK_WINDOW,
        )

    ratelimit.hit_all(
        ratelimit.caller_keys("chat", _Req()),
        ratelimit.CHAT_LIMIT, ratelimit.CHAT_WINDOW,
    )  # must not raise


# -- the concurrency guard, as the engine uses it --------------------------
#
# `test_llm_provider.py` covers the slot itself. This covers the other half:
# a correct guard the engine never acquires protects nothing, the same way a
# correct limiter the route never calls does.


@pytest.fixture
def one_slot(monkeypatch):
    import threading

    from services.providers import llm

    monkeypatch.setattr(llm, "MAX_CONCURRENCY", 1)
    monkeypatch.setattr(llm, "QUEUE_WAIT", 0.05)
    monkeypatch.setattr(llm, "_slots", threading.BoundedSemaphore(1))
    return llm


def test_the_engine_takes_a_slot_before_running_the_tool_loop(
    fitted_app, one_slot, monkeypatch
):
    # monkeypatch, not assignment: `fitted_app` is session-scoped, so a bare
    # `engine.librarian.answer = ...` would follow the suite into every later
    # test. That is the Phase A fixture bug, which cost ten golden baselines.
    main_module, _ = fitted_app
    engine = getattr(main_module.RECOMMENDER, "chatbot", None)
    if engine is None or engine.librarian is None:
        pytest.skip("chatbot engine or librarian unavailable")

    called = []
    monkeypatch.setattr(
        engine.librarian, "answer", lambda *a, **k: called.append(1)
    )

    with one_slot.slot():                      # capacity is gone
        response = engine.respond("recommend me something")

    assert called == [], "the tool loop ran with no slot available"
    assert response["mode"] == "classifier", (
        "saturation must degrade to the classifier, not fail the reply"
    )
    assert response["message"], "section 12: the reader still gets an answer"


def test_a_saturated_model_does_not_leak_the_slot_for_the_next_caller(
    fitted_app, one_slot, monkeypatch
):
    """The refusal path must not consume the capacity it was refused."""
    main_module, _ = fitted_app
    engine = getattr(main_module.RECOMMENDER, "chatbot", None)
    if engine is None or engine.librarian is None:
        pytest.skip("chatbot engine or librarian unavailable")

    def boom(*a, **k):
        raise RuntimeError("the tool loop blew up")

    monkeypatch.setattr(engine.librarian, "answer", boom)

    with pytest.raises(RuntimeError):
        engine.respond("recommend me something")

    with one_slot.slot():
        pass  # capacity came back
