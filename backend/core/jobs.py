"""Bounded background job registry — closes F-19.

The problem
-----------
`POST /api/audiobook/generate` synthesised speech **in the request thread**,
with no timeout. This is not theoretical: writing the OI-5 rate-limit tests,
three calls hung the whole test suite past 120 seconds. Three. That is the
denial-of-service shape reproduced by accident, and no credentials were
needed to pull the lever.

Why a queue alone would not have fixed it
----------------------------------------
Moving work off the request thread stops requests from *blocking*, but an
unbounded queue just relocates the exhaustion: a client can still enqueue
thousands of jobs and occupy the workers indefinitely. So the registry is
bounded at both ends — a small worker pool, and a hard cap on work waiting to
start. When it is saturated the endpoint says 503 with `Retry-After` rather
than accepting work it has no intention of doing soon.

Deliberately in-process, not Redis or Postgres
----------------------------------------------
A job's useful lifetime is one synthesis, tens of seconds. Persisting state
would make jobs look durable without making them durable: TTS cannot resume
in a process that died, so a "running" row surviving a restart describes work
that will never finish. An empty registry after a restart is the truthful
answer.

The cost is a real constraint, not a hidden one: **this works for a single
worker process.** With several, a job accepted by one is invisible to the
others and polling 404s. That is recorded against OI-5 as a deployment gate,
alongside the same limitation the rate limiter degrades to. Moving the
registry to Redis is the fix when multi-worker becomes real; it is not needed
before then.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

# Two at a time. gTTS is network-bound, so this is not about CPU; it is about
# refusing to let one client hold every slot.
MAX_WORKERS = 2

# Work allowed to be waiting. Past this the answer is 503, not a longer queue.
MAX_PENDING = 8

# How long a finished job stays readable. Long enough for a client to poll and
# fetch, short enough that the registry cannot grow without bound.
RESULT_TTL_SECONDS = 900

# A job that has been running longer than this is presumed wedged. gTTS calls
# have no timeout of their own, which is the other half of why F-19 hung.
JOB_TIMEOUT_SECONDS = 120


@dataclass
class Job:
    id: str
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"job_id": self.id, "status": self.status}
        if self.status == DONE:
            body["result"] = self.result
        if self.status == FAILED:
            body["error"] = self.error
        if self.started_at and not self.finished_at:
            body["running_for_seconds"] = round(time.time() - self.started_at, 1)
        return body


class Saturated(RuntimeError):
    """Too much work already waiting. The caller should back off, not queue."""

    def __init__(self, pending: int) -> None:
        super().__init__(
            f"{pending} audiobook jobs already waiting; try again shortly"
        )
        self.retry_after = 30


class JobRegistry:
    def __init__(
        self,
        max_workers: int = MAX_WORKERS,
        max_pending: int = MAX_PENDING,
        ttl: int = RESULT_TTL_SECONDS,
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="job"
        )
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._max_pending = max_pending
        self._ttl = ttl

    # -- queries ----------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            # Report a wedged job as failed rather than leaving a client
            # polling "running" forever. The thread may still be stuck; the
            # bounded pool is what stops that from mattering.
            if (
                job.status == RUNNING
                and job.started_at
                and time.time() - job.started_at > JOB_TIMEOUT_SECONDS
            ):
                job.status = FAILED
                job.error = f"timed out after {JOB_TIMEOUT_SECONDS}s"
                job.finished_at = time.time()
            return job

    def pending(self) -> int:
        with self._lock:
            return sum(
                1 for j in self._jobs.values() if j.status in (QUEUED, RUNNING)
            )

    # -- submission -------------------------------------------------------

    def submit(self, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> Job:
        self._reap()
        with self._lock:
            pending = sum(
                1 for j in self._jobs.values() if j.status in (QUEUED, RUNNING)
            )
            if pending >= self._max_pending:
                raise Saturated(pending)
            job = Job(id=uuid.uuid4().hex)
            self._jobs[job.id] = job

        self._pool.submit(self._run, job, fn, args, kwargs)
        return job

    def _run(self, job: Job, fn: Callable[..., dict], args: tuple, kwargs: dict) -> None:
        with self._lock:
            job.status = RUNNING
            job.started_at = time.time()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # a failed job must not kill the worker
            log.warning(f"job {job.id} failed: {type(exc).__name__}: {exc}")
            with self._lock:
                job.status = FAILED
                job.error = f"{type(exc).__name__}: {exc}"
                job.finished_at = time.time()
            return
        with self._lock:
            job.status = DONE if result.get("ok", True) else FAILED
            if job.status == FAILED:
                job.error = str(result.get("error") or result.get("message") or "failed")
            job.result = result
            job.finished_at = time.time()

    # -- housekeeping -----------------------------------------------------

    def _reap(self) -> None:
        cutoff = time.time() - self._ttl
        with self._lock:
            stale = [
                jid
                for jid, j in self._jobs.items()
                if j.finished_at is not None and j.finished_at < cutoff
            ]
            for jid in stale:
                del self._jobs[jid]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


REGISTRY = JobRegistry()
