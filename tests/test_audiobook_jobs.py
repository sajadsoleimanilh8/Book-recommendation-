"""F-19 — audiobook generation must not run in the request thread.

The bug demonstrated itself: the first version of the rate-limit tests hung
the whole suite past 120 seconds on three calls, because each one performed a
network fetch and a TTS call inline with no timeout. Three unauthenticated
requests were enough.

These tests hold the fix in place: the request returns immediately with a job
to poll, the queue is bounded so the exhaustion cannot simply move into it,
and a job that fails is reported rather than swallowed.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import jobs  # noqa: E402

BODY = {"book_id": 1, "book_name": "Animal Farm", "language": "en"}


@pytest.fixture
def app_client(fitted_app, monkeypatch):
    """The real endpoint with a stubbed synthesiser and no rate limit.

    The limiter has its own tests; letting it fire here would mask what these
    are checking.
    """
    main_module, client = fitted_app
    if not getattr(main_module.RECOMMENDER, "audiobook", None):
        pytest.skip("audiobook engine unavailable")
    monkeypatch.setattr(main_module.ratelimit, "hit", lambda *a, **k: None)
    # A registry per test, so saturation in one cannot leak into another.
    monkeypatch.setattr(jobs, "REGISTRY", jobs.JobRegistry())
    monkeypatch.setattr(main_module.jobs, "REGISTRY", jobs.REGISTRY)
    return main_module, client


def _stub(main_module, monkeypatch, fn):
    monkeypatch.setattr(main_module.RECOMMENDER.audiobook, "generate", fn)


def _wait_for(client, job_id, want=("done", "failed"), timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/audiobook/jobs/{job_id}").json()
        if body["status"] in want:
            return body
        time.sleep(0.05)
    pytest.fail(f"job {job_id} never reached {want}")


def test_generate_returns_202_with_a_job_to_poll(app_client, monkeypatch):
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: {"ok": True, "file": "x.mp3"})

    response = client.post("/api/audiobook/generate", json=BODY)

    assert response.status_code == 202
    body = response.json()
    assert body["job_id"]
    assert body["poll"] == f"/api/audiobook/jobs/{body['job_id']}"
    assert response.headers["Location"] == body["poll"]


def test_the_request_does_not_wait_for_synthesis(app_client, monkeypatch):
    """The whole point of F-19. Synthesis takes a second here; the POST must
    not.
    """
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: (time.sleep(1.0), {"ok": True})[1])

    started = time.perf_counter()
    response = client.post("/api/audiobook/generate", json=BODY)
    elapsed = time.perf_counter() - started

    assert response.status_code == 202
    assert elapsed < 0.5, (
        f"POST blocked for {elapsed:.2f}s — generation is still running in the "
        "request thread"
    )
    # And the work really does happen, rather than being dropped.
    assert _wait_for(client, response.json()["job_id"])["status"] == "done"


def test_a_finished_job_carries_its_result(app_client, monkeypatch):
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: {"ok": True, "file": "animal-farm.mp3"})

    job_id = client.post("/api/audiobook/generate", json=BODY).json()["job_id"]
    body = _wait_for(client, job_id)

    assert body["status"] == "done"
    assert body["result"]["file"] == "animal-farm.mp3"


def test_a_crashing_generator_fails_the_job_not_the_poll(app_client, monkeypatch):
    """A job that raises must be reported. Swallowing it leaves a client
    polling something that will never finish."""
    main_module, client = app_client

    def boom(**kw):
        raise RuntimeError("tts exploded")

    _stub(main_module, monkeypatch, boom)

    job_id = client.post("/api/audiobook/generate", json=BODY).json()["job_id"]
    body = _wait_for(client, job_id)

    assert body["status"] == "failed"
    assert "tts exploded" in body["error"]


def test_a_generator_reporting_failure_is_not_recorded_as_done(app_client, monkeypatch):
    """`{"ok": False}` is a failure even though nothing raised."""
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: {"ok": False, "error": "no text"})

    job_id = client.post("/api/audiobook/generate", json=BODY).json()["job_id"]
    assert _wait_for(client, job_id)["status"] == "failed"


def test_an_unbounded_queue_would_only_move_the_problem(app_client, monkeypatch):
    """Backgrounding the work is not sufficient on its own.

    Without a cap a client can enqueue thousands of jobs and occupy the
    workers indefinitely — the same denial of service, one layer down. Past
    the cap the honest answer is 503, not a longer queue.
    """
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: (time.sleep(5.0), {"ok": True})[1])

    statuses = [
        client.post("/api/audiobook/generate", json=BODY).status_code
        for _ in range(jobs.MAX_PENDING + 3)
    ]

    assert 503 in statuses, f"queue accepted everything: {statuses}"
    assert statuses.index(503) >= jobs.MAX_PENDING, (
        "refused before the queue was actually full"
    )


def test_the_503_carries_retry_after(app_client, monkeypatch):
    main_module, client = app_client
    _stub(main_module, monkeypatch, lambda **kw: (time.sleep(5.0), {"ok": True})[1])

    for _ in range(jobs.MAX_PENDING + 3):
        response = client.post("/api/audiobook/generate", json=BODY)
        if response.status_code == 503:
            assert int(response.headers["Retry-After"]) > 0
            return
    pytest.fail("never saturated")


def test_polling_an_unknown_job_404s(app_client):
    _, client = app_client
    assert client.get("/api/audiobook/jobs/nosuchjob").status_code == 404


def test_a_wedged_job_is_reported_failed_rather_than_running_forever(monkeypatch):
    """gTTS has no timeout of its own — the other half of why F-19 hung."""
    registry = jobs.JobRegistry()
    monkeypatch.setattr(jobs, "JOB_TIMEOUT_SECONDS", 0.2)

    job = registry.submit(lambda: (time.sleep(3.0), {"ok": True})[1])
    time.sleep(0.4)

    assert registry.get(job.id).status == "failed"
    assert "timed out" in registry.get(job.id).error
