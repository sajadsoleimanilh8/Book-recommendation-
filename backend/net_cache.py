"""Process-wide DNS cache for the enrichment passes — F-32.

Why this exists
---------------
Measured, not guessed. During a three-pass parallel run, 229 of 233 Gutenberg
failures were a single error:

    <urlopen error [Errno 11001] getaddrinfo failed>

A 67% failure rate looks exactly like a provider blocking us, and that is how
it was first read. It was not. Resolving the same three hostnames twelve times
each from one idle process succeeds 36/36 in 1.3 seconds — the names are fine.
What fails is doing it thousands of times concurrently from three processes:
`urllib` opens a fresh connection per request, so every single fetch pays a
fresh DNS lookup, and the Windows resolver starts refusing under that churn.
The same cause explains the WinError 10013 ("socket access forbidden") entries,
which are ephemeral-socket exhaustion wearing a different hat.

So the passes were not being throttled by Gutenberg or Open Library. They were
throttled by their own DNS traffic, and the circuit breakers were dutifully
stopping runs over a self-inflicted wound.

The fix
-------
These passes talk to three hostnames for hours. Caching resolution collapses
tens of thousands of lookups into a handful. A TTL keeps it honest for a
long-running process if a record actually changes.

Deliberately not a connection pool. That would mean replacing `urllib`
throughout the provider layer, and this addresses 229 of the 233 observed
failures on its own.
"""

from __future__ import annotations

import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_TTL = 600.0

_lock = threading.Lock()
_cache: dict[tuple, tuple[float, list]] = {}
_original = None
_stats = {"hits": 0, "misses": 0}


def install(ttl: float = DEFAULT_TTL) -> None:
    """Wrap socket.getaddrinfo with a TTL cache. Idempotent."""
    global _original
    if _original is not None:
        return
    _original = socket.getaddrinfo

    def cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        key = (host, port, family, type, proto, flags)
        now = time.monotonic()

        with _lock:
            entry = _cache.get(key)
            if entry and entry[0] > now:
                _stats["hits"] += 1
                return entry[1]

        # Resolve outside the lock: a slow lookup must not block every other
        # thread, and a concurrent duplicate resolve is cheaper than a stall.
        result = _original(host, port, family, type, proto, flags)

        with _lock:
            _cache[key] = (now + ttl, result)
            _stats["misses"] += 1
        return result

    socket.getaddrinfo = cached_getaddrinfo
    log.info(f"DNS cache installed (ttl={ttl:.0f}s)")


def uninstall() -> None:
    """Restore the real resolver. Used by tests."""
    global _original
    if _original is not None:
        socket.getaddrinfo = _original
        _original = None
    with _lock:
        _cache.clear()
        _stats.update(hits=0, misses=0)


def stats() -> dict:
    with _lock:
        return {**_stats, "entries": len(_cache)}
