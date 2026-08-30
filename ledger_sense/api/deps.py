"""Session and ownership dependencies.

`register` is the only sanctioned way to mount a router, and it applies
`require_session` unconditionally. A router mounted any other way fails
test_every_api_route_requires_a_session — which is how F-25 gets caught the day
it recurs rather than at week 12.

`owned` raises 404, never 403. A 403 tells a prober the resource exists.
"""

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute

from ledger_sense.auth.session import Session, load_session


def require_session(request: Request) -> Session:
    token = request.cookies.get("session")
    session = load_session(token) if token else None
    if session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return session


def owned(session: Session, resource_tenant_id: str | None) -> None:
    if resource_tenant_id is None or resource_tenant_id != session.tenant_id:
        raise HTTPException(status_code=404, detail="not found")


def register(app: FastAPI, router: APIRouter) -> None:
    """Mount `router` on `app` with require_session on every one of its routes.

    Adds each route individually rather than calling app.include_router:
    since 0.141 FastAPI keeps an included router as a single opaque
    _IncludedRouter entry in app.routes and resolves its paths and
    dependencies lazily at match time. The behaviour is the same; the route
    table stops being walkable, and a walkable route table is the entire
    mechanism behind "no route without a session" — the audit test would go
    on passing with every route hidden from it. Verified: with
    include_router, test_every_api_route_requires_a_session passes over an
    empty walk. Live requests stay guarded either way — FastAPI still
    applies the router's dependencies at match time — it is only the
    app.routes introspection that goes blind.

    test_the_audit_walk_covers_every_served_route pins that down from the
    other side, comparing the walk against the OpenAPI surface, so a future
    FastAPI change here fails loudly instead of silently emptying the audit.
    """
    guard = Depends(require_session)
    for route in router.routes:
        if not isinstance(route, APIRoute):
            raise TypeError(f"register() mounts API routes only, got {route!r}")
        # ponytail: carries the APIRoute attributes this codebase's routers
        # set. A router that sets response_class, callbacks, openapi_extra or
        # generate_unique_id_function needs them added to this list — the
        # route still mounts and is still guarded, it just loses that
        # decoration.
        app.router.add_api_route(
            route.path,  # already carries the router's prefix
            route.endpoint,
            methods=sorted(route.methods) if route.methods else None,
            dependencies=[guard, *route.dependencies],
            response_model=route.response_model,
            status_code=route.status_code,
            tags=route.tags,
            summary=route.summary,
            description=route.description,
            responses=route.responses,
            deprecated=route.deprecated,
            include_in_schema=route.include_in_schema,
            name=route.name,
        )
