"""Request middleware — the undeclared-query-parameter guard (F-38).

Lifted from `main.py` in the Phase D restructure. RESTRUCTURE-PROMPT
section 2 lists middleware among what main.py legitimately holds, but
this one carries ~90 lines of defect commentary (F-38, the _iter_routes
bug found mid-restructure), which is most of what pushed main.py past
its line budget. The three helpers and the guard move here; main.py
calls install() once, right after it has registered every router.

One necessary transformation, not a behaviour change: the guard read
the module-global `app` to reach `app.router.routes`. Moved out, it
reads `request.app` instead — the same application object, which
Starlette puts on every request scope. Verified: `/api/books?bogus=x`
still 422s.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from starlette.routing import Match

# --------------------------------------------------------------------------
# F-38 — an undeclared query parameter is a 422, not a shrug.
#
# `GET /api/books?max_price=5` returned all 29,975 books with HTTP 200, not
# because the filter was broken but because the route never declared
# `max_price` and FastAPI discards parameters it does not know about. The
# caller believes they filtered, the server says 200, and the data disagrees.
# Every typo — `ratingmin`, `max_pages`, `limt` — behaves the same way.
#
# This is the same failure shape as OI-11 one layer up, and the reason to fix
# it now is that it gets more expensive with every client added.
#
# Implemented as middleware rather than per-route so a route added later
# cannot forget it. It resolves the route itself, because Starlette has not
# matched one yet at middleware time.
# --------------------------------------------------------------------------

_QUERY_PARAM_ALLOWLIST = {
    # Cache-busting parameters that HTTP clients append on their own. These
    # are not the caller expressing intent, so rejecting them would fail
    # requests over something the caller did not write.
    "_",
}


def _declared_query_params(dependant) -> set[str]:
    """Every query parameter a route accepts, including via sub-dependencies."""
    names = {p.alias or p.name for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _declared_query_params(sub)
    return names


def _iter_routes(routes):
    """Flatten app.router.routes, descending into included routers.

    Found while extracting the health and books routers (Phase D): this
    FastAPI version wraps every `app.include_router(...)` call in an
    `_IncludedRouter` object that has no `.dependant` and no `.path` of
    its own — only an `.original_router` holding the real `APIRoute`
    list. Before this helper, `reject_undeclared_query_params` walked
    `app.router.routes` directly, saw `dependant is None` on every
    included router's wrapper, and `continue`d past it without ever
    checking the routes inside — silently disabling unknown-query
    -parameter rejection for every route registered through a router,
    not just the one being extracted at the time. Confirmed empirically
    against a live app: `/api/books?max_price=5` returned 200 instead of
    422 the moment `/api/books` moved into a router.

    Not present before this restructure because nothing was registered
    through `include_router()` except auth, which takes no query
    parameters — the gap was real but untestable until a
    query-validated route moved. Duck-typed on `.original_router` rather
    than importing FastAPI's private `_IncludedRouter` class, and
    recursive in case a future FastAPI version nests routers more than
    one level deep.
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_routes(original.routes)
        else:
            yield route


async def reject_undeclared_query_params(request: Request, call_next):
    if request.query_params:
        for route in _iter_routes(request.app.router.routes):
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            match, _ = route.matches(request.scope)
            if match != Match.FULL:
                continue
            declared = _declared_query_params(dependant) | _QUERY_PARAM_ALLOWLIST
            unknown = sorted(set(request.query_params.keys()) - declared)
            if unknown:
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    content={
                        "error": {
                            "message": (
                                "Unknown query parameter(s): "
                                + ", ".join(unknown)
                            ),
                            "code": "unknown_query_parameter",
                            "unknown": unknown,
                            "accepted": sorted(declared - _QUERY_PARAM_ALLOWLIST),
                        }
                    },
                )
            break
    return await call_next(request)


def install(app: FastAPI) -> None:
    """Register the query-parameter guard on the app."""
    app.middleware("http")(reject_undeclared_query_params)
