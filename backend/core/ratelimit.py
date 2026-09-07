"""Per-IP rate limiting — OI-5, the blocking deployment gate.

Why this endpoint specifically
------------------------------
`POST /api/audiobook/generate` is unauthenticated (F-07 covered the rest of
the API, not this) and synchronous (F-19): each call fetches a book and
synthesises speech in the request thread. A handful of repeat calls occupies
every worker, so the endpoint is a denial-of-service lever that needs no
credentials to pull. That is why OI-5 blocks deployment rather than merely
being advisable.

This closes the rate-limit half. F-19 — moving generation to a background job
— is still open, and OI-5 stays blocking until it lands.

Fixed window, not a token bucket
--------------------------------
A fixed window lets through up to 2x the limit across a window boundary. That
is the textbook objection, and it does not matter here: the point is to stop
one client occupying every worker, and "at most 2x of a very small number" is
still a very small number. A fixed window is two Redis commands and is
obviously correct by inspection; a sliding window is a sorted set, three
commands and a cleanup policy. Section 8.

What happens when Redis is down
-------------------------------
It falls back to an in-process counter and says so, once, at WARNING.

Failing open was rejected: Redis is most likely to be down under exactly the
load a rate limit exists to survive, so "open" means no protection when it is
needed most. Failing closed was also rejected: a cache outage should not take
the whole endpoint down (section 12). The in-process fallback is correct for a
single instance, which is what this deployment is, and degrades to per-worker
limits if it is ever scaled out — noted here so that is a known property
rather than a surprise.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from fastapi import Request

from core import config

log = logging.getLogger(__name__)

# Generation takes seconds of CPU. Three per minute is generous for a human
# and useless for a script.
AUDIOBOOK_LIMIT = 3
AUDIOBOOK_WINDOW = 60

_redis = None
_redis_checked = False
_warned_fallback = False

_lock = threading.Lock()
_local: dict[str, list[float]] = defaultdict(list)


class RateLimited(Exception):
    """Raised with the seconds the caller should wait."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry in {retry_after}s")
        self.retry_after = retry_after


def _get_redis():
    global _redis, _redis_checked
    if _redis_checked:
        return _redis
    _redis_checked = True
    try:
        import redis

        client = redis.Redis.from_url(config.REDIS_URL, socket_timeout=0.25)
        client.ping()
        _redis = client
        log.info("rate limiter using Redis")
    except Exception as exc:
        log.warning(f"rate limiter falling back to in-process counters: {exc}")
        _redis = None
    return _redis


def client_ip(request: Request) -> str:
    """The caller's address.

    `X-Forwarded-For` is deliberately ignored. It is attacker-controlled
    unless a trusted proxy overwrites it, so honouring it here would let
    anyone reset their own limit by inventing a header — a rate limiter that
    can be bypassed with one line of curl is worse than none, because it
    reads as protection. When this sits behind a real proxy, that proxy's
    trusted header should be read here, deliberately.
    """
    return request.client.host if request.client else "unknown"


def _hit_redis(client, key: str, limit: int, window: int) -> None:
    pipe = client.pipeline()
    pipe.incr(key)
    pipe.expire(key, window, nx=True)  # only on creation, so the window is fixed
    count, _ = pipe.execute()
    if count > limit:
        raise RateLimited(int(client.ttl(key)) or window)


def _hit_local(key: str, limit: int, window: int) -> None:
    global _warned_fallback
    if not _warned_fallback:
        log.warning("rate limiting in-process; correct for one instance only")
        _warned_fallback = True

    now = time.monotonic()
    with _lock:
        hits = [t for t in _local[key] if now - t < window]
        if len(hits) >= limit:
            _local[key] = hits
            raise RateLimited(int(window - (now - hits[0])) + 1)
        hits.append(now)
        _local[key] = hits


def hit(key: str, limit: int, window: int) -> None:
    """Record one request against `key`. Raises RateLimited when over."""
    client = _get_redis()
    if client is None:
        return _hit_local(key, limit, window)
    try:
        return _hit_redis(client, key, limit, window)
    except RateLimited:
        raise
    except Exception as exc:
        # Redis went away mid-flight. Degrade rather than 500 the caller.
        log.warning(f"redis rate limit failed, using in-process: {exc}")
        return _hit_local(key, limit, window)


def reset() -> None:
    """Clear all counters. Tests only."""
    global _redis, _redis_checked, _warned_fallback
    with _lock:
        _local.clear()
    if _redis is not None:
        try:
            for key in _redis.scan_iter("ratelimit:*"):
                _redis.delete(key)
        except Exception:
            pass
    _redis, _redis_checked, _warned_fallback = None, False, False
