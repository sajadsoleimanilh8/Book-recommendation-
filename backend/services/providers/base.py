"""Provider abstraction — audit section C.4, spec section 17.

One normalized shape, one HTTP path, one place for the cross-cutting
concerns. Adapters only map provider JSON into `NormalizedBook`.

Two deliberate omissions, per section 8 (no speculative infrastructure):

* **No `get_availability`.** Section 18 is explicit that metadata providers
  and availability providers are different concepts, and that with no
  provider configured the answer is `unknown`, never fabricated. No
  availability provider is configured, so the adapter would have nothing
  behind it.
* **No Redis response cache.** `books.enrichment_status` in Postgres already
  stops the backfill re-fetching anything it has done, which is the only
  repeated-request pattern that exists today. A cache earns its place when
  there is a live user-facing lookup path — semantic search, Phase 2 later —
  not before.

Retry is *not* optional here. The very first live call to Google Books
returned 503; the same query succeeded on retry. Transient failures are the
normal case at this volume.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

USER_AGENT = "DigiKitab/1.0 (+https://github.com/digikitab) enrichment"

# 429 and 5xx are worth retrying; 400/403/404 are not — retrying a bad
# request just burns quota.
RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class NormalizedBook:
    """Provider-independent book metadata.

    Every field is optional: providers disagree about what they know, and a
    partial record is still useful. `source` records which adapter produced
    it so reconciliation can apply precedence.
    """

    source: str
    external_id: str | None = None
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    description: str | None = None
    categories: list[str] = field(default_factory=list)
    isbn_10: str | None = None
    isbn_13: str | None = None
    language: str | None = None
    page_count: int | None = None
    published_year: int | None = None
    thumbnail: str | None = None
    average_rating: float | None = None
    ratings_count: int | None = None

    @property
    def author(self) -> str | None:
        return ", ".join(self.authors) if self.authors else None

    def has_content(self) -> bool:
        """Whether this record carries anything worth writing.

        A description is the point of Phase 2 (F-15); ISBNs are the point of
        reconciliation and the F-25 recheck. Everything else is a bonus.
        """
        return bool(self.description or self.isbn_13 or self.isbn_10)


class BookProvider(Protocol):
    """Spec section 17, minus get_availability — see the module docstring."""

    name: str

    def search_books(self, query: str, filters: dict | None = None) -> list[NormalizedBook]: ...

    def get_book(self, provider_id: str) -> NormalizedBook | None: ...

    def get_by_isbn(self, isbn: str) -> NormalizedBook | None: ...


class RateLimiter:
    """Simple spacing limiter.

    Not a token bucket: the goal is to avoid tripping a per-minute limit
    during a long sequential backfill, and even spacing does that with less
    machinery. The per-day budget is enforced by the caller, which is where
    the resumable state lives.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


class QuotaExceeded(RuntimeError):
    """Raised when a provider says the daily quota is gone.

    Distinct from a generic failure: the caller must stop the whole run and
    resume tomorrow, not mark this one book as failed and continue burning
    requests that cannot succeed.
    """


class ProviderThrottled(RuntimeError):
    """Raised when a provider is refusing sustained traffic.

    Google Books does not answer 429 when you push too hard — it answers
    **503 "Service temporarily unavailable"**, indistinguishable at a glance
    from a genuine outage. Observed live: the first ~285 books enriched fine,
    then every request returned 503 within a second.

    Treating that as an ordinary retryable error is actively harmful. Each
    book then burns its full retry ladder (4 attempts with backoff, across up
    to 3 requests) and the run appears to hang while achieving nothing. The
    caller must trip a circuit breaker instead.
    """


class ProviderUnreachable(ProviderThrottled):
    """We never reached the provider — DNS, socket, or timeout failure.

    Distinct from "the provider answered and does not have this book". That
    distinction is the whole point: `fetch_json` used to return None for both,
    so a DNS blip made `enrich_one` see "no match" and write
    `enrichment_status = 'not_found'`, permanently excluding a book that the
    provider may well have. Subclassing ProviderThrottled means the caller
    already does the right thing — leave the row pending and retry later.
    """


def fetch_json(
    url: str,
    *,
    limiter: RateLimiter | None = None,
    timeout: float = 20.0,
    max_attempts: int = 4,
) -> dict[str, Any] | None:
    """GET JSON with spacing, retry and backoff.

    Returns None for 404 and for permanent client errors — "not found" is an
    ordinary outcome during a backfill, not an exception. Raises
    QuotaExceeded when the provider reports the daily limit is gone.
    """
    for attempt in range(1, max_attempts + 1):
        if limiter:
            limiter.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)

        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read()[:400].decode("utf-8", "replace")
            except Exception:
                pass

            if exc.code == 429 and "per day" in body.lower():
                raise QuotaExceeded(f"daily quota exhausted: {body[:200]}") from exc

            if exc.code == 404:
                return None

            if exc.code == 403:
                raise ProviderUnreachable(
                    f"HTTP 403 (blocked, not a miss): {body[:120]}"
                ) from exc

            if exc.code not in RETRY_STATUS:
                log.warning(f"HTTP {exc.code} for {url.split('?')[0]}: {body[:120]}")
                return None

            if attempt == max_attempts:
                # Retries exhausted on a throttling-shaped status. Signal it
                # rather than returning None, so the caller can stop the run
                # instead of grinding through thousands more books that will
                # each waste the same four attempts.
                raise ProviderThrottled(
                    f"HTTP {exc.code} after {attempt} attempts: {body[:120]}"
                ) from exc

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == max_attempts:
                raise ProviderUnreachable(
                    f"unreachable after {attempt} attempts: {exc}"
                ) from exc

        # Exponential backoff with jitter, so a provider hiccup does not turn
        # into a synchronised retry storm across a long run.
        time.sleep(min(2 ** attempt, 16) * (0.5 + random.random()))

    return None


# --------------------------------------------------------------------------
# Query normalisation
# --------------------------------------------------------------------------
#
# Gutenberg records carry librarian-style metadata that defeats exact-phrase
# provider search:
#
#     author  "W. B. (William Butler) Yeats"   parenthetical expansion
#     author  "Mrs. Rowson"                    honorific
#     title   "Love-at-arms :  being a narrative excerpted from the chron"
#                                              truncated subtitle, stray colon
#
# Measured effect on an Open Library sample: 0/6 matched raw, 3/6 matched
# normalised. It does not conjure descriptions that a provider does not hold
# — but a book that is never matched cannot yield an ISBN or a year either.

_INITIALS_EXPANSION = re.compile(r"^\s*(?:[A-Z]\.\s*)+\((.+?)\)\s*(.+)$")
_HONORIFIC = re.compile(r"^(Mrs?\.|Dr\.|Sir|Lady|Rev\.)\s+", re.IGNORECASE)
_SUBTITLE = re.compile(r"\s+[:\-—]\s+")
_VOLUME_TAIL = re.compile(r"\s*volume\s+\w+.*$", re.IGNORECASE)


def normalise_author(author: str | None) -> str | None:
    """First credited author, in a form providers can match."""
    if not author:
        return None
    first = author.split(",")[0].strip()
    if not first or first.lower() in {"unknown", "unknown author", "anonymous"}:
        return None
    expanded = _INITIALS_EXPANSION.match(first)
    if expanded:
        # "W. B. (William Butler) Yeats" -> "William Butler Yeats"
        first = f"{expanded.group(1)} {expanded.group(2)}".strip()
    return _HONORIFIC.sub("", first).strip() or None


def normalise_title(title: str | None) -> str | None:
    """Drop subtitles and volume tails, which providers rarely index."""
    if not title:
        return None
    main = _SUBTITLE.split(title)[0]
    main = _VOLUME_TAIL.sub("", main)
    main = re.sub(r"[^\w\s',.]", " ", main)
    return " ".join(main.split()).strip(" .,:;-") or None


# --------------------------------------------------------------------------
# ISBN handling
# --------------------------------------------------------------------------


def clean_isbn(value: Any) -> str | None:
    """Strip separators and validate length. Returns None if not an ISBN."""
    if not value:
        return None
    s = "".join(ch for ch in str(value).strip().upper() if ch.isalnum())
    if len(s) == 10 and s[:9].isdigit() and (s[9].isdigit() or s[9] == "X"):
        return s
    if len(s) == 13 and s.isdigit():
        return s
    return None


def isbn_10_to_13(isbn10: str) -> str | None:
    """Convert so records carrying only an ISBN-10 still join on isbn_13.

    Without this, reconciliation misses every pair where one provider
    returned a 10 and the other a 13 — common for pre-2007 titles, which is
    a large share of a catalogue full of public-domain and older books.
    """
    isbn10 = clean_isbn(isbn10) or ""
    if len(isbn10) != 10:
        return None
    core = "978" + isbn10[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)
