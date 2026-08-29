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
