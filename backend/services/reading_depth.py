"""Reading depth — F-26's relevance target.

Decided by the product owner on 2026-09-11: relevance is **how far readers get
into a book**, normalised and aggregated per book across everyone who engaged
with it. Chosen because it was the only candidate signal capturable with no
login and no new UI, and because it measures what the product claims to care
about (§6) rather than curiosity about a title.

What it measures, precisely
---------------------------
`book_texts` holds the opening chapters only — 20,358 characters on average,
about 11 pages. So depth is **"did the opening of this book hold readers"**,
normalised by *excerpt* length. Normalising by the whole book would put a
reader who finished every available page of a 300-page novel at ~4%, and
compress every book into a sliver of the range.

The identity problem, and how it is solved
------------------------------------------
There is no login and no visitor id, so a page turn cannot be attributed to a
reader, and "each reader's furthest page" cannot be computed directly.

It does not need to be. The reader requests pages in order, so the number of
requests for page *p* approximates the number of readers who reached *p*. That
is a survival curve, and the mean furthest page falls out of it:

    mean pages reached  =  Σ survival(p) / survival(1)

Two corrections make it robust:

* **Monotone survival.** Flipping back to re-read page 3 adds a request for
  page 3 without anyone going further, which would let a later page appear to
  hold more readers than an earlier one. `survival(p)` is capped at
  `survival(p-1)`, so back-flips cannot manufacture depth.
* **Only genuine turns with content.** `page_size == 1` separates a reader's
  turn from a bulk fetch, and `had_content` excludes the empty pages the pager
  offers past the end of an excerpt.

Scope
-----
Book-level only — which books hold readers, not which reader. Personalisation
arrives with login (OI-6).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, Tuple

# How many readers a book needs before its measured depth counts as much as
# the popularity prior does. Below this, the prior dominates; above it, the
# evidence does. See `shrink`.
PRIOR_STRENGTH = 5.0


def depth_from_page_counts(
    page_counts: Dict[int, int], excerpt_pages: int
) -> Tuple[float, int]:
    """(depth in [0, 1], readers who started) from per-page request counts.

    0.0 means every reader stopped on page one; 1.0 means every reader reached
    the last page of the excerpt. Returns ``(0.0, 0)`` when nobody opened page
    one — a book reached only by deep link has no measurable starting point,
    and treating that as zero depth would be the absent-as-negative mistake.
    """
    if excerpt_pages < 1:
        return 0.0, 0

    starts = int(page_counts.get(1, 0))
    if starts <= 0:
        return 0.0, 0

    if excerpt_pages == 1:
        # A one-page excerpt is finished by opening it.
        return 1.0, starts

    survival = starts
    reached_total = survival
    for page in range(2, excerpt_pages + 1):
        # Monotone: a page cannot hold more readers than the one before it.
        survival = min(int(page_counts.get(page, 0)), survival)
        reached_total += survival

    mean_pages_reached = reached_total / starts
    depth = (mean_pages_reached - 1.0) / (excerpt_pages - 1)
    return max(0.0, min(1.0, depth)), starts


def shrink(depth: float, readers: float, prior: float, strength: float = PRIOR_STRENGTH) -> float:
    """Blend measured depth with the popularity prior, by amount of evidence.

        relevance = (readers * depth + strength * prior) / (readers + strength)

    With no readers this is exactly the prior, so a book nobody has read keeps
    the ranking it has today — and with no traffic at all, the whole target is
    unchanged. As readers accumulate the target moves towards measured depth,
    and one enthusiastic reader cannot outweigh a well-established prior.
    """
    return (readers * depth + strength * prior) / (readers + strength)


def aggregate(
    events: Iterable[Tuple[Any, Dict[str, Any]]]
) -> Dict[Any, Tuple[float, int]]:
    """{book_key: (depth, readers)} from (book_key, context) event pairs.

    Pure, so the whole pipeline is testable without Postgres. Events that are
    not genuine turns with content are ignored here, not at write time — the
    raw log keeps them, which is what makes content gaps visible.
    """
    counts: Dict[Any, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
    excerpt: Dict[Any, int] = {}

    for key, context in events:
        if not context or key is None:
            continue
        if context.get("page_size") != 1 or not context.get("had_content"):
            continue
        page = int(context.get("page") or 0)
        pages = int(context.get("excerpt_pages") or 0)
        if page < 1 or pages < 1 or page > pages:
            continue
        counts[key][page] += 1
        # The excerpt length should be constant per book; if a text was
        # re-fetched and changed length, the latest observation is the truth.
        excerpt[key] = pages

    return {
        key: depth_from_page_counts(per_page, excerpt[key])
        for key, per_page in counts.items()
    }


def load_reading_depth(session) -> Dict[Tuple[str, str], Tuple[float, int]]:
    """{(source, external_id): (depth, readers)} from the interaction log.

    Keyed the way the catalogue identifies books (F-27's `(source,
    external_id)`), so the result can be attached to the in-memory catalogue
    before the recommender fits — the only point at which it can reach the
    training target.
    """
    from sqlalchemy import select

    from models import Book, InteractionEvent
    import events

    rows = session.execute(
        select(Book.source, Book.external_id, InteractionEvent.context)
        .join(Book, Book.id == InteractionEvent.book_id)
        .where(InteractionEvent.event_type == events.READING_PAGE)
    ).all()
    return aggregate(
        ((str(source), str(external_id).strip()), context)
        for source, external_id, context in rows
    )
