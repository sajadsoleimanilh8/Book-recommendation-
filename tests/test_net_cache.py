"""DNS cache tests — F-32.

The bug this guards is a misdiagnosis, not just a slowdown. Uncached lookups
under a parallel run produced a 67% `getaddrinfo failed` rate, which read as
"Gutenberg is blocking us" and tripped the circuit breakers. It was our own
DNS traffic. These tests pin the behaviour that makes that impossible.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import net_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    net_cache.uninstall()
    yield
    net_cache.uninstall()


def _fake_resolver(counter):
    def resolve(host, port, family=0, type=0, proto=0, flags=0):
        counter.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]

    return resolve


def test_repeat_lookups_resolve_once(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver(calls))
    net_cache.install(ttl=300)

    for _ in range(200):
        socket.getaddrinfo("www.gutenberg.org", 443)

    assert len(calls) == 1, f"expected 1 real resolution, got {len(calls)}"
    assert net_cache.stats()["hits"] == 199


def test_distinct_hosts_are_not_conflated(monkeypatch):
    """A cache that returned one host's address for another would be far worse
    than the bug it fixes."""
    calls: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver(calls))
    net_cache.install(ttl=300)

    socket.getaddrinfo("www.gutenberg.org", 443)
    socket.getaddrinfo("openlibrary.org", 443)
    socket.getaddrinfo("www.googleapis.com", 443)
    socket.getaddrinfo("www.gutenberg.org", 443)

    assert calls == ["www.gutenberg.org", "openlibrary.org", "www.googleapis.com"]


def test_entries_expire(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(socket, "getaddrinfo", _fake_resolver(calls))

    clock = [1000.0]
    monkeypatch.setattr(net_cache.time, "monotonic", lambda: clock[0])
    net_cache.install(ttl=60)

    socket.getaddrinfo("openlibrary.org", 443)
    clock[0] += 30
    socket.getaddrinfo("openlibrary.org", 443)
    assert len(calls) == 1, "still inside the TTL"

    clock[0] += 31
    socket.getaddrinfo("openlibrary.org", 443)
    assert len(calls) == 2, "past the TTL, must re-resolve"


def test_failures_are_not_cached(monkeypatch):
    """A cached failure would turn one DNS blip into a permanent outage for
    the rest of the run — the opposite of the point."""
    attempts = []

    def flaky(host, port, *a, **kw):
        attempts.append(host)
        if len(attempts) == 1:
            raise socket.gaierror(11001, "getaddrinfo failed")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", flaky)
    net_cache.install(ttl=300)

    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("openlibrary.org", 443)

    socket.getaddrinfo("openlibrary.org", 443)  # must retry, not serve a cached error
    assert len(attempts) == 2


def test_uninstall_restores_the_real_resolver(monkeypatch):
    original = socket.getaddrinfo
    net_cache.install()
    assert socket.getaddrinfo is not original
    net_cache.uninstall()
    assert socket.getaddrinfo is original


def test_install_is_idempotent():
    net_cache.install()
    wrapped = socket.getaddrinfo
    net_cache.install()
    assert socket.getaddrinfo is wrapped, "double install would nest the wrappers"
