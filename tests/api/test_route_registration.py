"""The control the given audit test is missing.

test_every_api_route_requires_a_session walks app.routes and asserts nothing
there lacks require_session. That is a negative assertion: it passes just as
happily when the walk sees nothing at all. It does exactly that under FastAPI
0.141, where include_router leaves one opaque _IncludedRouter in app.routes
instead of the routes themselves — every route hidden, audit green, anonymous
callers served. deps.register() adds routes individually so the table stays
walkable; these tests are what stop that from silently regressing.
"""

from fastapi.routing import APIRoute

from ledger_sense.api.app import UNGUARDED_PREFIXES as UNGUARDED
from ledger_sense.api.app import app


def _walked() -> set[str]:
    """The /api paths test_every_api_route_requires_a_session can see."""
    return {route.path for route in app.routes
            if isinstance(route, APIRoute) and route.path.startswith("/api/")}


def test_the_audit_walk_covers_every_served_route() -> None:
    """A route the walk cannot see is a route the audit cannot fail on.

    OpenAPI enumerates the whole served surface however routers were mounted,
    so it is the one description of "what this app answers" that does not
    depend on the route table's shape.
    """
    served = {path for path in app.openapi()["paths"] if path.startswith("/api/")}
    assert served - _walked() == set(), "served but invisible to the session audit"


def test_the_walk_is_not_empty() -> None:
    """Belt and braces: openapi() and app.routes could both go empty together."""
    protected = {path for path in _walked() if not path.startswith(UNGUARDED)}
    assert len(protected) >= 6, protected


def test_every_protected_route_carries_the_guard_in_its_dependencies() -> None:
    """The same property the given audit asserts, stated positively."""
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/") or route.path.startswith(UNGUARDED):
            continue
        names = {dependency.dependency.__name__ for dependency in route.dependencies
                 if dependency.dependency is not None}
        assert "require_session" in names, route.path
