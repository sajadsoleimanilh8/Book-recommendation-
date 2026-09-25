"""Per-caller rate limiting — OI-5, the blocking deployment gate.

Which endpoints, and why those
------------------------------
Two, both expensive and both reachable without credentials:

`POST /api/audiobook/generate` was the original one. It is unauthenticated
(F-07 covered the rest of the API, not this) and was synchronous: each call
fetched a book and synthesised speech in the request thread. F-19 has since
moved generation to a background job, so the limit now bounds how fast jobs
can be *queued* rather than how many workers one caller can occupy. Still
worth having — submitting is cheap, the work it schedules is not.

`POST /api/chat` (and its `/chatbot` alias) is the newer and now the worse
one. F-22 made it the most expensive unauthenticated endpoint in the app: it
takes `OptionalUser`, so anonymous callers are supported by design, and each
request can drive up to `MAX_STEPS` tool-calling round trips against a local
model on a single GPU. A per-caller limit alone does not protect that GPU —
see `services.chat` for the concurrency guard that does — but it stops one
caller monopolising the queue.

What a limit is keyed on
------------------------
The IP always, and the account as well when there is one. The IP bucket is
the ceiling that matters, because it is what an attacker has to spend real
resources to multiply. The account bucket exists only so that a caller who
*does* have many addresses cannot use one account across all of them; with
the same limit on both it never binds otherwise. Shared-NAT callers share an
IP bucket, which is the accepted cost of the ceiling being meaningful.

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
from typing import Optional

from fastapi import Request

from core import config

log = logging.getLogger(__name__)

# Generation takes seconds of CPU. Three per minute is generous for a human
# and useless for a script.
AUDIOBOOK_LIMIT = 3
AUDIOBOOK_WINDOW = 60

# A person composing a message to a librarian sends one every 15-30 seconds.
# Ten a minute leaves that untouched and still caps a scripted caller at ten
# tool loops a minute rather than as many as the socket will carry.
CHAT_LIMIT = 10
CHAT_WINDOW = 60

# Phase 4, section 29. Authenticated (unlike the two above), so the threat
# is a careless or scripted caller filling the disk, not an anonymous flood.
# One book is a deliberate act; ten in an hour is generous for a person
# building a library and pointless for a script.
UPLOAD_LIMIT = 10
UPLOAD_WINDOW = 3600

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


def caller_keys(scope: str, request: Request, account_id: Optional[int] = None) -> list[str]:
    """Every bucket one request is charged against.

    Always the address; additionally the account when the caller is signed
    in. See the module docstring for why both — in short, the IP bucket is
    the ceiling and the account bucket closes address-roaming. Charging both
    means the caller is held to whichever is tighter.
    """
    keys = [f"ratelimit:{scope}:ip:{client_ip(request)}"]
    if account_id is not None:
        keys.append(f"ratelimit:{scope}:u:{account_id}")
    return keys


def hit_all(keys: list[str], limit: int, window: int) -> None:
    """Charge every bucket, refusing on the first that is full.

    Buckets after a refusal are left uncharged; ones already charged stay
    charged, so a refusal by a later bucket still costs an earlier one a
    slot. The IP goes first precisely so that asymmetry falls the harmless
    way: the only way to be refused by the account bucket while inside the
    IP bucket is to be roaming addresses, and over-charging an address in
    that case is not a cost worth a second round trip to avoid.
    """
    for key in keys:
        hit(key, limit, window)


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
