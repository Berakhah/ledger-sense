"""The application object.

Four routes live outside the session boundary and only four, all of them
mounted by hand in build_app() below so the whole exception list is one
function you can read top to bottom:

  GET  /api/health              — a container orchestrator has to reach it
                                  before anyone can log in.
  POST /api/auth/login          — how a session comes to exist.
  POST /api/fixtures/seed       — mounted ONLY when
                                  LEDGER_SENSE_PROFILE == "demo"; absent, not
                                  merely guarded, under every other value
                                  including unset. See api/fixtures.py.
  POST /api/reconciliation/seed — same demo-only discipline as the fixtures
                                  seed route, for the reconciliation screen's
                                  in-memory store. See api/reconciliation.py.

Everything else is mounted through deps.register(), which applies
require_session — see tests/api/test_auth.py::test_every_api_route_requires_a_session,
which walks the route table and fails on anything mounted another way. The
four above are mounted with add_api_route rather than include_router so they
stay visible to that walk instead of hiding from the audit that exempts them.
"""

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ledger_sense.api._stub_routes import stub_routers
from ledger_sense.api.baseline import router as baseline_router
from ledger_sense.api.deps import register
from ledger_sense.api.fixtures import SEED_PATH, demo_profile_enabled, seed
from ledger_sense.api.inbox import router as inbox_router
from ledger_sense.api.reconciliation import RECONCILIATION_SEED_PATH
from ledger_sense.api.reconciliation import router as reconciliation_router
from ledger_sense.api.reconciliation import seed_demo as reconciliation_seed
from ledger_sense.api.review import router as review_router
from ledger_sense.api.writeback import router as writeback_router
from ledger_sense.auth.models import authenticate
from ledger_sense.auth.session import SESSION_TTL, issue_session
from ledger_sense.ingest.limits import DEFAULT_LIMITS

# next.config.ts: "the browser talks to it directly at NEXT_PUBLIC_API_URL;
# nothing is proxied through Next" — the web app is a separate origin (see
# docker-compose.yml) that calls this API with fetch() from the baseline
# screen onward, so the browser enforces CORS on every one of those calls.
# Credentials (the session cookie) are required, so this cannot be "*".
WEB_ORIGIN = os.environ.get("LEDGER_SENSE_WEB_ORIGIN", "http://localhost:3000")

LOGIN_LIMIT = 10
LOGIN_WINDOW_SECONDS = 60.0

# The exceptions named in the module docstring, as data, because two separate
# audit suites assert against them (tests/api/test_auth.py and
# tests/api/test_route_registration.py) and a third hand-written copy is how
# F-55 happened. It lives here rather than in a test because it is this
# module's claim about itself: adding an entry is a change to the session
# boundary and should read like one in the diff. /api/reconciliation/seed
# joined /api/fixtures/seed here for exactly that reason (Task 32) — it mints
# its own session the same way, and its GET sibling
# (/api/reconciliation/{doc_id}) is NOT in this tuple: that one goes through
# register() like every other real route.
UNGUARDED_PREFIXES = ("/api/health", "/api/auth", "/api/fixtures",
                      RECONCILIATION_SEED_PATH)

# ponytail: in-process, per-worker. Two uvicorn workers means two counters and
# an effective 20/minute; a shared counter (Redis, or a sessions-table row)
# is the upgrade when the deployment stops being one container. It is a
# brute-force brake, not an access control — the ownership check is.
_login_hits: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()

# I3: Starlette's own `max_part_size` (the multipart parser's own guard)
# bounds a non-file *field*, not a file part — a file part spools to disk
# (tmpfs under docker-compose.yml's read_only root, inside the 1 GB
# mem_limit) with no upstream size limit at all. Without this, a multi-
# gigabyte POST fills that tmpfs and OOMs the container long before
# inbox.py's own 413 ever gets a chance to fire on the parsed body. This
# constant is the multipart-framing slack (boundary lines, headers, the
# `origin` form field) allowed on top of the raw file cap the route itself
# enforces — not a second, independent size policy.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
_INBOX_UPLOAD_PATH = "/api/inbox"
_INBOX_UPLOAD_METHOD = "POST"


class InboxBodyCapMiddleware:
    """Refuses an oversized or lengthless POST /api/inbox body before
    Starlette's multipart parser ever spools a byte of it to disk.

    A plain ASGI callable, not `BaseHTTPMiddleware`: `BaseHTTPMiddleware`
    buffers the whole request body into memory to hand the inner app a
    `Request`, which is exactly the unbounded read this middleware exists to
    prevent. Scoped to the exact path and method a file upload actually
    reaches — every other route (`/api/health`, `/api/auth/login`, ...)
    passes straight through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope["method"] != _INBOX_UPLOAD_METHOD
                or scope["path"] != _INBOX_UPLOAD_PATH):
            await self._app(scope, receive, send)
            return

        # {"detail": ...} — the same body shape FastAPI's own HTTPException
        # produces, so a client cannot tell this rejection apart from one
        # the route itself raised.
        content_length = Headers(scope=scope).get("content-length")
        if content_length is None:
            # No Content-Length means a chunked (or otherwise length-less)
            # body: this route requires an upfront declared size to refuse
            # an oversized upload before reading it, so a client that will
            # not declare one is refused outright rather than trusted.
            response = JSONResponse(
                {"detail": "a Content-Length header is required; chunked "
                           "request bodies are not accepted"},
                status_code=411)
            await response(scope, receive, send)
            return

        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > DEFAULT_LIMITS.max_bytes + MULTIPART_OVERHEAD_BYTES:
            response = JSONResponse(
                {"detail": f"rejected: declared request size {declared} exceeds the size cap"},
                status_code=413)
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


class LoginRequest(BaseModel):
    # Plain str, not EmailStr: pydantic's email validation needs a dependency
    # (email-validator) to reject strings the lookup rejects anyway.
    email: str
    password: str


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    with _login_lock:
        hits = _login_hits[client]
        while hits and now - hits[0] > LOGIN_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= LOGIN_LIMIT:
            return True
        hits.append(now)
        return False


def build_app() -> FastAPI:
    app = FastAPI(title="Ledger Sense")

    # Ordering matters: Starlette's add_middleware inserts at the front of
    # the user_middleware list, and the *last* middleware added ends up
    # OUTERMOST (it wraps every other middleware, including the app).
    # InboxBodyCapMiddleware's 411/413 responses are returned directly by
    # the middleware itself, short-circuiting the stack below it — so if
    # CORSMiddleware were added after it (outside it), those responses would
    # never pass through CORSMiddleware and would reach the browser with no
    # Access-Control-Allow-Origin header. The web app is a separate origin
    # calling this API with fetch()+credentials (see WEB_ORIGIN above), so a
    # missing CORS header on that response is not a cosmetic gap: the
    # browser reports an opaque network error instead of the 413/411 detail.
    # Registering the body cap FIRST keeps CORSMiddleware outermost, so every
    # response — including the body cap's own — passes through it.
    app.add_middleware(InboxBodyCapMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[WEB_ORIGIN],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/auth/login")
    def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
        if _rate_limited(request.client.host if request.client else "unknown"):
            raise HTTPException(status_code=429, detail="too many attempts")
        identity = authenticate(body.email, body.password)
        if identity is None:
            # One message for both "no such user" and "wrong password":
            # anything narrower enumerates accounts.
            raise HTTPException(status_code=401, detail="invalid credentials")
        user_id, tenant_id = identity
        token = issue_session(user_id, tenant_id)
        response.set_cookie(
            "session",
            token,
            max_age=int(SESSION_TTL.total_seconds()),
            httponly=True,      # not readable from JavaScript
            samesite="strict",  # not sent on a cross-site request: CSRF brake
            secure=True,        # HTTPS only
            path="/",
        )
        # The token also comes back in the body for non-browser clients, which
        # is a real weakening of HttpOnly — anything that can read this
        # response has the session. Browser callers should ignore it and let
        # the cookie do the work.
        return {"token": token}

    # Read at build time, not per request: whether this process is a demo
    # cannot change under it, and a per-request read would let an env change
    # open the hatch on a running client deployment.
    if demo_profile_enabled():
        app.add_api_route(SEED_PATH, seed, methods=["POST"], status_code=201,
                          tags=["fixtures"])
        app.add_api_route(RECONCILIATION_SEED_PATH, reconciliation_seed, methods=["POST"],
                          status_code=201, tags=["fixtures"])

    # /api/baseline, /api/inbox, /api/review and /api/reconciliation's GET
    # route are not among the UNGUARDED_PREFIXES exceptions above (unlike
    # /api/fixtures/seed and /api/reconciliation/seed) and follow the same
    # profile discipline as any other normal route: register() applies
    # require_session, and each is available whichever profile this process
    # runs under.
    register(app, baseline_router)
    register(app, inbox_router)
    register(app, review_router)
    register(app, reconciliation_router)
    register(app, writeback_router)

    for router in stub_routers():
        register(app, router)

    return app


app = build_app()
